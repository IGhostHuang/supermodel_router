"""
agent_state.py -- Persistent state layer for the SMR v0.5.0 Agent framework.

Provides:
    * Dataclasses: ``Entity``, ``PlanStep``, ``Plan``, ``ToolCall`` with
      ``to_dict`` / ``from_dict`` JSON-safe serialization (datetime -> ISO).
    * ``AgentStateStore``: SQLite-backed persistence for plans, steps,
      tool calls, and entities.  All public methods are ``async def``,
      protected by an ``asyncio.Lock``.  Uses the stdlib ``sqlite3``
      (concurrency is low, no need for ``aiosqlite``).
    * ``EntityExtractor``: Lightweight keyword/pattern extractor that
      pulls proper-noun style entities (ALL CAPS, CamelCase) out of free
      text.  Configurable via a known-entity allowlist.

The store uses a single SQLite database file.  Default location:
``data/agent_state.db`` (relative to the current working directory).

Usage::

    from supermodel_router.agent_state import AgentStateStore, Entity

    store = AgentStateStore()                     # data/agent_state.db
    plan_id = await store.create_plan(Plan(...))
    await store.add_step(plan_id, PlanStep(...))
    await store.checkpoint(step_id, "intermediate observation")
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("agent_state")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = Path("data") / "agent_state.db"
DEFAULT_LIST_LIMIT = 20

# Status enums for PlanStep
STEP_STATUS_PENDING = "pending"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_DONE = "done"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_SKIPPED = "skipped"

VALID_STEP_STATUSES = {
    STEP_STATUS_PENDING,
    STEP_STATUS_RUNNING,
    STEP_STATUS_DONE,
    STEP_STATUS_FAILED,
    STEP_STATUS_SKIPPED,
}

# Patterns for entity extraction
ALL_CAPS_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,})\b")           # TAOS, HTTP, GPT4
CAMEL_CASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{1,}[A-Z][a-zA-Z0-9]*)\b")  # HermesApi, OpenRouter


# ---------------------------------------------------------------------------
# Optional config import (kept tolerant of missing config.py)
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    from .config import CONFIG  # type: ignore
except Exception:  # noqa: BLE001
    CONFIG = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> float:
    """Current epoch seconds."""
    return time.time()


def _iso(ts: Optional[float]) -> str:
    """Convert an epoch timestamp to ISO format (UTC)."""
    if ts is None or ts == 0:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _new_id(prefix: str) -> str:
    """Generate a short prefixed UUID."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Entity:
    """A named entity (model, service, project, concept) tracked by the agent."""

    id: str
    name: str
    entity_type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "created_at": self.created_at,
            "created_at_iso": _iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Entity":
        return cls(
            id=d.get("id") or _new_id("ent"),
            name=d.get("name", ""),
            entity_type=d.get("entity_type", "unknown"),
            properties=dict(d.get("properties") or {}),
            created_at=float(d.get("created_at") or 0.0),
        )


@dataclass
class PlanStep:
    """A single executable step within a plan."""

    id: str
    description: str
    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: str = STEP_STATUS_PENDING
    result: Optional[str] = None
    observation: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "tool_name": self.tool_name,
            "params": self.params,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "result": self.result,
            "observation": self.observation,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "started_at_iso": _iso(self.started_at),
            "finished_at_iso": _iso(self.finished_at),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanStep":
        return cls(
            id=d.get("id") or _new_id("step"),
            description=d.get("description", ""),
            tool_name=d.get("tool_name", ""),
            params=dict(d.get("params") or {}),
            depends_on=list(d.get("depends_on") or []),
            status=d.get("status", STEP_STATUS_PENDING),
            result=d.get("result"),
            observation=d.get("observation"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
        )


@dataclass
class Plan:
    """An agent plan: a user message plus ordered steps and tracked entities."""

    id: str
    user_msg: str
    steps: List[PlanStep] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    created_at: float = 0.0
    finished_at: Optional[float] = None
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_msg": self.user_msg,
            "steps": [s.to_dict() for s in self.steps],
            "entities": [e.to_dict() for e in self.entities],
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "created_at_iso": _iso(self.created_at),
            "finished_at_iso": _iso(self.finished_at),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Plan":
        return cls(
            id=d.get("id") or _new_id("plan"),
            user_msg=d.get("user_msg", ""),
            steps=[PlanStep.from_dict(s) for s in d.get("steps") or []],
            entities=[Entity.from_dict(e) for e in d.get("entities") or []],
            created_at=float(d.get("created_at") or 0.0),
            finished_at=d.get("finished_at"),
            status=d.get("status", "active"),
        )


@dataclass
class ToolCall:
    """A recorded tool invocation by the agent."""

    id: str
    plan_id: str
    step_id: str
    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "params": self.params,
            "result": self.result,
            "success": self.success,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "started_at_iso": _iso(self.started_at),
            "finished_at_iso": _iso(self.finished_at),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=d.get("id") or _new_id("call"),
            plan_id=d.get("plan_id", ""),
            step_id=d.get("step_id", ""),
            tool_name=d.get("tool_name", ""),
            params=dict(d.get("params") or {}),
            result=d.get("result"),
            success=bool(d.get("success", True)),
            error=d.get("error"),
            started_at=float(d.get("started_at") or 0.0),
            finished_at=d.get("finished_at"),
        )


# ---------------------------------------------------------------------------
# AgentStateStore
# ---------------------------------------------------------------------------
class AgentStateStore:
    """SQLite-backed persistent state for plans, steps, entities, and tool calls.

    All public methods are ``async def`` and serialised behind an
    ``asyncio.Lock``.  Internally the store uses the synchronous ``sqlite3``
    module: the expected concurrency is low (single agent loop), so
    ``aiosqlite`` is not required.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the sync connection can be used from
        # multiple threads if a future caller wraps us in an executor; the
        # asyncio.Lock still serialises logical operations.
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._sqlite_lock = threading.Lock()
        self._init_schema()
        LOG.info("AgentStateStore: opened db at %s", self._db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        with self._sqlite_lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    id           TEXT PRIMARY KEY,
                    user_msg     TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'active',
                    created_at   REAL NOT NULL,
                    finished_at  REAL
                );

                CREATE TABLE IF NOT EXISTS steps (
                    id           TEXT PRIMARY KEY,
                    plan_id      TEXT NOT NULL,
                    description  TEXT NOT NULL DEFAULT '',
                    tool_name    TEXT NOT NULL DEFAULT '',
                    params       TEXT NOT NULL DEFAULT '{}',
                    depends_on   TEXT NOT NULL DEFAULT '[]',
                    status       TEXT NOT NULL DEFAULT 'pending',
                    result       TEXT,
                    observation  TEXT,
                    started_at   REAL,
                    finished_at  REAL,
                    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_steps_plan_id ON steps(plan_id);

                CREATE TABLE IF NOT EXISTS entities (
                    id           TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    entity_type  TEXT NOT NULL DEFAULT 'unknown',
                    properties   TEXT NOT NULL DEFAULT '{}',
                    created_at   REAL NOT NULL,
                    UNIQUE(name, entity_type)
                );

                CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id           TEXT PRIMARY KEY,
                    plan_id      TEXT NOT NULL,
                    step_id      TEXT NOT NULL,
                    tool_name    TEXT NOT NULL,
                    params       TEXT NOT NULL DEFAULT '{}',
                    result       TEXT,
                    success      INTEGER NOT NULL DEFAULT 1,
                    error        TEXT,
                    started_at   REAL NOT NULL,
                    finished_at  REAL
                );

                CREATE INDEX IF NOT EXISTS idx_tool_calls_plan_id
                    ON tool_calls(plan_id);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_step_id
                    ON tool_calls(step_id);

                CREATE TABLE IF NOT EXISTS checkpoints (
                    step_id      TEXT PRIMARY KEY,
                    observation  TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    created_at   REAL NOT NULL,
                    FOREIGN KEY (step_id) REFERENCES steps(id) ON DELETE CASCADE
                );
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers (sync DB access)
    # ------------------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._sqlite_lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            self._conn.commit()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._sqlite_lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone()

    def _query_all(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._sqlite_lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> PlanStep:
        return PlanStep(
            id=row["id"],
            description=row["description"] or "",
            tool_name=row["tool_name"] or "",
            params=json.loads(row["params"] or "{}"),
            depends_on=json.loads(row["depends_on"] or "[]"),
            status=row["status"],
            result=row["result"],
            observation=row["observation"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"],
            name=row["name"],
            entity_type=row["entity_type"],
            properties=json.loads(row["properties"] or "{}"),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_plan(row: sqlite3.Row, steps: Optional[List[PlanStep]] = None) -> Plan:
        return Plan(
            id=row["id"],
            user_msg=row["user_msg"],
            steps=steps or [],
            entities=[],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            status=row["status"],
        )

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------
    async def create_plan(self, plan: Plan) -> str:
        """Insert a plan.  Returns the plan_id (also stored on ``plan.id``)."""
        async with self._lock:
            if not plan.id:
                plan.id = _new_id("plan")
            if not plan.created_at:
                plan.created_at = _now()
            self._execute(
                "INSERT INTO plans (id, user_msg, status, created_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan.id, plan.user_msg, plan.status, plan.created_at, plan.finished_at),
            )
            LOG.info("AgentStateStore: created plan '%s'", plan.id)
            return plan.id

    async def update_plan(self, plan_id: str, **kwargs: Any) -> None:
        """Update mutable fields on a plan row."""
        if not kwargs:
            return
        allowed = {"user_msg", "status", "finished_at"}
        sets = [f"{k} = ?" for k in kwargs if k in allowed]
        if not sets:
            return
        params: List[Any] = [kwargs[k] for k in kwargs if k in allowed]
        params.append(plan_id)
        async with self._lock:
            self._execute(
                f"UPDATE plans SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )

    async def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Fetch a plan and its steps."""
        async with self._lock:
            row = self._query_one(
                "SELECT * FROM plans WHERE id = ?", (plan_id,)
            )
            if row is None:
                return None
            steps = self._query_all(
                "SELECT * FROM steps WHERE plan_id = ? ORDER BY started_at, id",
                (plan_id,),
            )
            return self._row_to_plan(row, [self._row_to_step(s) for s in steps])

    async def list_plans(self, limit: int = DEFAULT_LIST_LIMIT) -> List[Plan]:
        """List recent plans (no steps populated for performance)."""
        async with self._lock:
            rows = self._query_all(
                "SELECT * FROM plans ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            )
            return [self._row_to_plan(r, []) for r in rows]

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------
    async def add_step(self, plan_id: str, step: PlanStep) -> None:
        """Insert a step belonging to a plan."""
        async with self._lock:
            if not step.id:
                step.id = _new_id("step")
            self._execute(
                "INSERT INTO steps (id, plan_id, description, tool_name, params, "
                "depends_on, status, result, observation, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    step.id,
                    plan_id,
                    step.description,
                    step.tool_name,
                    json.dumps(step.params),
                    json.dumps(step.depends_on),
                    step.status,
                    step.result,
                    step.observation,
                    step.started_at,
                    step.finished_at,
                ),
            )

    async def update_step(
        self,
        step_id: str,
        status: str,
        result: Optional[str] = None,
        observation: Optional[str] = None,
    ) -> None:
        """Update a step's status / result / observation."""
        if status not in VALID_STEP_STATUSES:
            raise ValueError(
                f"invalid step status '{status}' "
                f"(expected one of {sorted(VALID_STEP_STATUSES)})"
            )
        async with self._lock:
            self._execute(
                "UPDATE steps SET status = ?, result = ?, observation = ?, "
                "started_at = COALESCE(started_at, ?), "
                "finished_at = CASE WHEN ? IN ('done','failed','skipped') "
                "THEN COALESCE(finished_at, ?) ELSE finished_at END "
                "WHERE id = ?",
                (
                    status,
                    result,
                    observation,
                    _now(),
                    status,
                    _now(),
                    step_id,
                ),
            )

    async def checkpoint(self, step_id: str, observation: str) -> None:
        """Persist the current step status plus an observation snapshot.

        Useful for resumption: if the agent restarts, it can read the
        latest checkpoint to continue from where it stopped.
        """
        async with self._lock:
            row = self._query_one(
                "SELECT status FROM steps WHERE id = ?", (step_id,)
            )
            if row is None:
                LOG.warning(
                    "AgentStateStore: checkpoint for unknown step '%s'", step_id
                )
                return
            status = row["status"]
            self._execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(step_id, observation, status, created_at) VALUES (?, ?, ?, ?)",
                (step_id, observation, status, _now()),
            )
            # Also store the observation back on the step itself
            self._execute(
                "UPDATE steps SET observation = ? WHERE id = ?",
                (observation, step_id),
            )

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------
    async def record_tool_call(self, call: ToolCall) -> None:
        """Persist a tool invocation record."""
        async with self._lock:
            if not call.id:
                call.id = _new_id("call")
            if not call.started_at:
                call.started_at = _now()
            self._execute(
                "INSERT INTO tool_calls (id, plan_id, step_id, tool_name, params, "
                "result, success, error, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call.id,
                    call.plan_id,
                    call.step_id,
                    call.tool_name,
                    json.dumps(call.params),
                    call.result,
                    1 if call.success else 0,
                    call.error,
                    call.started_at,
                    call.finished_at,
                ),
            )

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------
    async def add_entity(self, entity: Entity) -> None:
        """Insert an entity (INSERT OR IGNORE on (name, entity_type))."""
        async with self._lock:
            if not entity.id:
                entity.id = _new_id("ent")
            if not entity.created_at:
                entity.created_at = _now()
            self._execute(
                "INSERT OR IGNORE INTO entities "
                "(id, name, entity_type, properties, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entity.id,
                    entity.name,
                    entity.entity_type,
                    json.dumps(entity.properties),
                    entity.created_at,
                ),
            )

    async def get_entity(self, name: str) -> Optional[Entity]:
        """Look up an entity by exact name (returns most recent match)."""
        async with self._lock:
            row = self._query_one(
                "SELECT * FROM entities WHERE name = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (name,),
            )
            return self._row_to_entity(row) if row else None

    async def search_entities(
        self, query: str, limit: int = 10
    ) -> List[Entity]:
        """Search entities by name substring (case-insensitive)."""
        like = f"%{query}%"
        async with self._lock:
            rows = self._query_all(
                "SELECT * FROM entities WHERE name LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, int(limit)),
            )
            return [self._row_to_entity(r) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._sqlite_lock:
            try:
                self._conn.close()
            except Exception as e:  # noqa: BLE001
                LOG.warning("AgentStateStore: close error: %s", e)

    def __enter__(self) -> "AgentStateStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# EntityExtractor
# ---------------------------------------------------------------------------
class EntityExtractor:
    """Lightweight entity extractor using ALL-CAPS and CamelCase regex.

    Optional configuration:
        * ``known_entities``: list of names to always treat as entities
          (e.g. ``["TAOS", "Hermes", "OpenRouter"]``).  When provided, the
          extractor first scans for these names literally (case-sensitive),
          then falls back to the regex heuristics for new entities.
        * ``entity_type``: entity_type to assign to extracted entities.
          Defaults to ``"proper_noun"``.
    """

    DEFAULT_KNOWN: List[str] = ["TAOS", "Hermes", "echo", "OpenRouter"]

    def __init__(
        self,
        known_entities: Optional[List[str]] = None,
        entity_type: str = "proper_noun",
    ):
        # Pull from CONFIG if available
        cfg_known: List[str] = []
        if CONFIG is not None:
            try:
                raw = CONFIG.get("agent_state", {}).get("known_entities", [])  # type: ignore[attr-defined]
                if isinstance(raw, list):
                    cfg_known = [str(x) for x in raw]
            except Exception:  # noqa: BLE001
                cfg_known = []

        merged = list(dict.fromkeys((known_entities or []) + cfg_known + self.DEFAULT_KNOWN))
        self._known = merged
        self._entity_type = entity_type

    def extract(self, text: str) -> List[Entity]:
        """Extract entities from ``text``.

        Returns a list of ``Entity`` (without ids/created_at -- those are
        filled in by ``AgentStateStore.add_entity``).
        """
        if not text:
            return []
        names: List[str] = []
        seen = set()

        def _add(name: str) -> None:
            if not name:
                return
            if name in seen:
                return
            seen.add(name)
            names.append(name)

        # 1. Known-entity literal scan (case-sensitive)
        for known in self._known:
            if known in text:
                _add(known)

        # 2. ALL-CAPS tokens (3+ chars)
        for m in ALL_CAPS_RE.finditer(text):
            _add(m.group(1))

        # 3. CamelCase tokens
        for m in CAMEL_CASE_RE.finditer(text):
            _add(m.group(1))

        now = _now()
        entities: List[Entity] = []
        for name in names:
            # Skip very short or all-digit matches
            if len(name) < 2 or name.isdigit():
                continue
            entities.append(Entity(
                id="",
                name=name,
                entity_type=self._entity_type,
                properties={"source": "EntityExtractor"},
                created_at=now,
            ))
        return entities


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors other modules' patterns)
# ---------------------------------------------------------------------------
_store: Optional[AgentStateStore] = None
_init_lock = asyncio.Lock()


async def init_agent_state_store(
    db_path: Optional[Path] = None,
) -> AgentStateStore:
    """Initialise (or return) the module-level ``AgentStateStore`` singleton."""
    global _store
    async with _init_lock:
        if _store is None:
            _store = AgentStateStore(db_path=db_path)
        return _store


def get_agent_state_store() -> Optional[AgentStateStore]:
    """Get the current store singleton (may be ``None`` if not yet init'd)."""
    return _store


def reset_agent_state_store() -> None:
    """Reset the singleton (primarily for tests)."""
    global _store
    if _store is not None:
        _store.close()
    _store = None
