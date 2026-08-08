"""tool_registry.py -- tool registration center for the SMR v0.5.0 Agent framework.

The ``ToolRegistry`` exposes tools to an LLM via OpenAI's ``tools`` parameter
format, executes them safely with timeouts, caches idempotent calls, and
protects the host from a failing tool with a per-tool circuit breaker.

Builtin tools (``basic`` and ``communication`` categories) cover file I/O,
globbing, HTTP GET, async subprocess, and a mock IM outbox.  Custom tools
can be added at runtime with ``register`` or bulk-loaded from a yaml dict.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional import: ``supermodel_router.config``
# ---------------------------------------------------------------------------
# The spec asks for ``from .config import ...`` style.  When this package is
# used in isolation (e.g. tests), ``config`` may be absent -- degrade
# gracefully so the rest of the module still imports.
try:  # pragma: no cover
    from .config import load_yaml_config, resolve_data_dir  # type: ignore
    _HAS_CONFIG = True
except Exception:  # ImportError / AttributeError
    _HAS_CONFIG = False

    def load_yaml_config(path: str) -> Dict[str, Any]:  # type: ignore
        raise RuntimeError("yaml config support requires supermodel_router.config")

    def resolve_data_dir() -> pathlib.Path:  # type: ignore
        return pathlib.Path("data").resolve()


LOG = logging.getLogger("tool_registry")

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
CATEGORY_BASIC = "basic"
CATEGORY_COMMUNICATION = "communication"
CATEGORY_MEMORY = "memory"
CATEGORY_SKILL = "skill"
VALID_CATEGORIES = {CATEGORY_BASIC, CATEGORY_COMMUNICATION, CATEGORY_MEMORY, CATEGORY_SKILL}

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT_S = 30.0
CACHE_TTL_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 2
MAX_FILE_BYTES = 100 * 1024
ECHO_OUTBOX_FILE = "echo_outbox.json"


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------
class CircuitOpenError(RuntimeError):
    """Raised when a tool has failed ``CIRCUIT_FAILURE_THRESHOLD`` times in a row."""

    def __init__(self, tool_name: str, consecutive_failures: int):
        self.tool_name = tool_name
        self.consecutive_failures = consecutive_failures
        super().__init__(
            f"Circuit open for tool '{tool_name}' "
            f"({consecutive_failures} consecutive failures)"
        )


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    """A tool exposed to the LLM.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable explanation for the LLM.
        parameters_schema: JSON-schema dict describing the arguments.
        handler: Async callable ``(**kwargs) -> Any``.
        category: One of basic / communication / memory / skill.
    """
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    category: str = CATEGORY_BASIC


@dataclass
class ToolResult:
    """Outcome of a single ``ToolRegistry.execute`` call.

    The registry wraps every handler exception into a ``ToolResult`` so the
    agent loop can react uniformly; only ``CircuitOpenError`` propagates.
    """
    success: bool
    output: str
    error: Optional[str] = None
    elapsed_ms: int = 0
    cached: bool = False


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------
class ToolRegistry:
    """Registry of tools available to an agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self.tool_call_cache: Dict[str, Tuple[float, ToolResult]] = {}
        self.circuit_breaker: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, tool: Tool) -> None:
        """Add or replace a tool."""
        if tool.category not in VALID_CATEGORIES:
            raise ValueError(
                f"unknown category '{tool.category}'; expected one of {sorted(VALID_CATEGORIES)}"
            )
        if not callable(tool.handler):
            raise TypeError(f"tool '{tool.name}' handler must be callable")
        LOG.info("ToolRegistry: registered '%s' (category=%s)", tool.name, tool.category)
        self._tools[tool.name] = tool

    def register_from_config(self, config_dict: Dict[str, Any]) -> int:
        """Bulk-register tools from a config dict (yaml-loaded).

        Expected shape::

            tools:
              - name: file_read
                category: basic
                description: ...
                parameters: {...}    # JSON-schema
                builtin: file_read   # use a builtin handler by name
        """
        count = 0
        for entry in config_dict.get("tools", []) or []:
            name = entry.get("name")
            if not name:
                LOG.warning("ToolRegistry: skipping entry without name: %r", entry)
                continue
            category = entry.get("category", CATEGORY_BASIC)
            description = entry.get("description", "")
            schema = entry.get("parameters", entry.get("parameters_schema", {}))
            builtin = entry.get("builtin")

            if builtin:
                base = _BUILTIN_FACTORY.get(builtin)
                if base is None:
                    LOG.warning("ToolRegistry: unknown builtin '%s' (tool '%s')", builtin, name)
                    continue
                tool = Tool(name=name, description=description or base.description,
                            parameters_schema=schema or base.parameters_schema,
                            handler=base.handler, category=category)
            else:
                async def _missing_handler(*_a: Any, **_kw: Any) -> str:
                    return f"[tool {name}] no handler wired up in config"

                tool = Tool(name=name, description=description, parameters_schema=schema,
                            handler=_missing_handler, category=category)
            self.register(tool)
            count += 1
        return count

    # ------------------------------------------------------------------
    # lookup / listing
    # ------------------------------------------------------------------
    def get(self, name: str) -> Tool:
        """Return the tool with this name, or raise ``KeyError``."""
        if name not in self._tools:
            raise KeyError(f"unknown tool '{name}'")
        return self._tools[name]

    def list(self, category: Optional[str] = None) -> List[Tool]:
        """Return all tools, optionally filtered by ``category``."""
        if category is None:
            return list(self._tools.values())
        return [t for t in self._tools.values() if t.category == category]

    def to_openai_tools_schema(self) -> List[Dict[str, Any]]:
        """Render every tool in OpenAI's ``tools`` parameter format."""
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters_schema,
            }}
            for t in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    async def execute(
        self,
        name: str,
        params: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> ToolResult:
        """Run ``name(**params)`` with caching, timeout, and circuit breaker."""
        tool = self.get(name)
        cache_key = _cache_key(name, params)

        cached = self._cache_lookup(cache_key)
        if cached is not None:
            LOG.debug("ToolRegistry: cache hit for '%s'", name)
            return cached

        failures = self.circuit_breaker.get(name, 0)
        if failures >= CIRCUIT_FAILURE_THRESHOLD:
            raise CircuitOpenError(name, failures)

        t0 = time.perf_counter()
        try:
            raw = await asyncio.wait_for(tool.handler(**(params or {})), timeout=timeout)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            result = ToolResult(success=True, output=_truncate(_stringify(raw)),
                                elapsed_ms=elapsed_ms)
            self._on_success(name)
            self._cache_store(cache_key, result)
            return result
        except asyncio.TimeoutError:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            err = f"timeout after {timeout:.1f}s"
            LOG.warning("ToolRegistry: '%s' %s", name, err)
            self._on_failure(name)
            return ToolResult(success=False, output="", error=err, elapsed_ms=elapsed_ms)
        except CircuitOpenError:
            raise
        except Exception as e:  # noqa: BLE001 -- wrap on purpose
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            LOG.exception("ToolRegistry: '%s' raised: %s", name, e)
            self._on_failure(name)
            return ToolResult(
                success=False, output="",
                error=f"{type(e).__name__}: {e}",
                elapsed_ms=elapsed_ms,
            )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _cache_lookup(self, key: str) -> Optional[ToolResult]:
        entry = self.tool_call_cache.get(key)
        if entry is None:
            return None
        ts, result = entry
        if (time.time() - ts) > CACHE_TTL_S:
            self.tool_call_cache.pop(key, None)
            return None
        result.cached = True
        return result

    def _cache_store(self, key: str, result: ToolResult) -> None:
        # Only cache successes so a transient failure isn't pinned.
        if not result.success:
            return
        self.tool_call_cache[key] = (time.time(), result)

    def _on_success(self, name: str) -> None:
        if self.circuit_breaker.get(name, 0) > 0:
            LOG.info("ToolRegistry: circuit reset for '%s'", name)
        self.circuit_breaker[name] = 0

    def _on_failure(self, name: str) -> None:
        self.circuit_breaker[name] = self.circuit_breaker.get(name, 0) + 1

    # ------------------------------------------------------------------
    # class-level helper
    # ------------------------------------------------------------------
    @classmethod
    def with_builtin_tools(cls) -> "ToolRegistry":
        """Return a registry pre-populated with the built-in tool set."""
        reg = cls()
        for tool in _BUILTIN_FACTORY.values():
            reg.register(tool)
        return reg


# ---------------------------------------------------------------------------
# utility helpers
# ---------------------------------------------------------------------------
def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _cache_key(name: str, params: Dict[str, Any]) -> str:
    payload = json.dumps({"name": name, "params": params},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# built-in tool implementations
# ---------------------------------------------------------------------------
async def _file_read(path: str) -> str:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if not p.is_file():
        raise IsADirectoryError(f"not a regular file: {path}")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({size} bytes > {MAX_FILE_BYTES})")
    return p.read_text(encoding="utf-8", errors="replace")


async def _file_write(path: str, content: str) -> str:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


async def _list_directory(path: str, pattern: str = "*") -> str:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"directory not found: {path}")
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    entries = sorted(p.glob(pattern))
    if not entries:
        return "(empty)"
    return "\n".join(e.name for e in entries)


async def _search_files(path: str, pattern: str, max_results: int = 50) -> str:
    p = pathlib.Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    matches: List[str] = []
    for entry in p.rglob(pattern):
        matches.append(str(entry))
        if len(matches) >= max_results:
            break
    return "\n".join(matches) if matches else "(no matches)"


async def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "smr-tool/0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return f"HTTP {resp.status}\n{body}"
    except urllib.error.URLError as e:
        raise RuntimeError(f"http_get failed: {e}") from e


async def _run_command(cmd: str, timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"command timed out after {timeout}s and was killed")
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    return f"[exit={proc.returncode}]\nstdout:\n{out}\nstderr:\n{err}".strip()


async def _echo_message(recipient: str, message: str) -> str:
    """Mock IM -- append to ``data/echo_outbox.json`` (Lark integration can replace this)."""
    outbox = resolve_data_dir() / ECHO_OUTBOX_FILE
    outbox.parent.mkdir(parents=True, exist_ok=True)
    entry = {"recipient": recipient, "message": message, "ts": time.time()}
    existing: List[Dict[str, Any]] = []
    if outbox.exists():
        try:
            existing = json.loads(outbox.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:  # noqa: BLE001
            existing = []
    existing.append(entry)
    outbox.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"queued message to {recipient} ({len(message)} chars)"


# ---------------------------------------------------------------------------
# builtin factory table
# ---------------------------------------------------------------------------
def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_BUILTIN_FACTORY: Dict[str, Tool] = {
    "file_read": Tool(
        name="file_read",
        description="Read the contents of a UTF-8 text file (max 100KB).",
        parameters_schema=_schema(
            {"path": {"type": "string", "description": "File path."}}, ["path"]),
        handler=_file_read, category=CATEGORY_BASIC,
    ),
    "file_write": Tool(
        name="file_write",
        description="Write text content to a file (creates parent dirs).",
        parameters_schema=_schema({
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Text content."},
        }, ["path", "content"]),
        handler=_file_write, category=CATEGORY_BASIC,
    ),
    "list_directory": Tool(
        name="list_directory",
        description="List entries in a directory matching a glob pattern.",
        parameters_schema=_schema({
            "path": {"type": "string", "description": "Directory to list."},
            "pattern": {"type": "string", "default": "*", "description": "Glob pattern."},
        }, ["path"]),
        handler=_list_directory, category=CATEGORY_BASIC,
    ),
    "search_files": Tool(
        name="search_files",
        description="Recursively search a directory for files matching a glob.",
        parameters_schema=_schema({
            "path": {"type": "string", "description": "Root directory."},
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py'."},
            "max_results": {"type": "integer", "default": 50},
        }, ["path", "pattern"]),
        handler=_search_files, category=CATEGORY_BASIC,
    ),
    "http_get": Tool(
        name="http_get",
        description="Perform an HTTP GET (urllib) and return the body.",
        parameters_schema=_schema({
            "url": {"type": "string", "description": "URL to fetch."},
            "timeout": {"type": "integer", "default": 15},
        }, ["url"]),
        handler=_http_get, category=CATEGORY_BASIC,
    ),
    "run_command": Tool(
        name="run_command",
        description="Run a shell command (async subprocess; hard-kill on timeout).",
        parameters_schema=_schema({
            "cmd": {"type": "string", "description": "Shell command string."},
            "timeout": {"type": "integer", "default": 30},
        }, ["cmd"]),
        handler=_run_command, category=CATEGORY_BASIC,
    ),
    "echo_message": Tool(
        name="echo_message",
        description="Mock IM -- queues a message into data/echo_outbox.json.",
        parameters_schema=_schema({
            "recipient": {"type": "string", "description": "Recipient handle."},
            "message": {"type": "string", "description": "Message body."},
        }, ["recipient", "message"]),
        handler=_echo_message, category=CATEGORY_COMMUNICATION,
    ),
}


# ---------------------------------------------------------------------------
# module-level singleton (mirrors context_compressor pattern)
# ---------------------------------------------------------------------------
_registry: Optional[ToolRegistry] = None


def init_tool_registry(with_builtin: bool = True) -> ToolRegistry:
    """Initialise the module-level ToolRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry.with_builtin_tools() if with_builtin else ToolRegistry()
    return _registry


def get_tool_registry() -> Optional[ToolRegistry]:
    """Return the current singleton (or ``None`` if not yet initialised)."""
    return _registry


def reset_tool_registry() -> None:
    """Reset the singleton -- primarily for tests."""
    global _registry
    _registry = None