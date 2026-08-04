"""
git_checkpoint.py -- lightweight file-system checkpoint / rollback for SMR.

Inspired by ``pi-git-checkpoint`` but deliberately **does not depend on Git**.
SMR runs inside Docker containers where a Git repo may not be initialised, so
we use plain file-system snapshots (copy + timestamp).

What gets snapshotted:
    * config.yaml / config.py        -- global routing configuration
    * state/model_health.json        -- health-tier state
    * fusion_metrics.json            -- per-plan metrics
    * Any arbitrary file path passed to ``create_checkpoint()``

Design principles:
    * **Atomic**: checkpoint directory is created fully before being "committed"
      (renamed from ``.tmp`` suffix).  A half-written checkpoint is invisible.
    * **Async-safe**: a lock serialises concurrent checkpoint creation and
      rollback operations.
    * **Diff-aware**: rollback can do a full restore or show what changed.
    * **Self-cleaning**: old checkpoints beyond ``max_checkpoints`` are pruned
      automatically (oldest first).

Usage::

    from supermodel_router.git_checkpoint import CheckpointManager

    mgr = CheckpointManager(
        state_dir="/data/smr/state",
        config_paths=["/data/smr/config.yaml", "/data/smr/state/model_health.json"],
    )

    # Before a risky config change
    cp = await mgr.create_checkpoint("before-fusion-strategy-update")

    # If something breaks
    await mgr.rollback(cp.checkpoint_id)

    # Inspect history
    checkpoints = mgr.list_checkpoints()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_CHECKPOINTS = 20
CHECKPOINT_PREFIX = "cp_"
TMP_SUFFIX = ".tmp"
MANIFEST_FILENAME = "manifest.json"


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------
@dataclass
class Checkpoint:
    """A single checkpoint snapshot.

    Attributes:
        checkpoint_id: Unique identifier (timestamp-based).
        label: Human-readable label describing why the checkpoint was created.
        created_at: Unix timestamp of creation.
        files: Dict mapping original file path -> snapshot path within the
               checkpoint directory.
        file_count: Number of files snapshotted.
    """

    checkpoint_id: str
    label: str
    created_at: float
    files: Dict[str, str] = field(default_factory=dict)
    file_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "label": self.label,
            "created_at": self.created_at,
            "created_at_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)
            ),
            "file_count": self.file_count,
            "files": self.files,
        }


@dataclass
class RollbackResult:
    """Result of a rollback operation.

    Attributes:
        success: Whether the rollback completed without errors.
        checkpoint_id: The checkpoint that was restored.
        restored_files: List of file paths that were restored.
        skipped_files: List of file paths that were skipped (e.g. missing in
                       checkpoint).
        error: Error message if success is False.
    """

    success: bool
    checkpoint_id: str
    restored_files: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "checkpoint_id": self.checkpoint_id,
            "restored_files": self.restored_files,
            "skipped_files": self.skipped_files,
            "error": self.error,
        }


@dataclass
class DiffEntry:
    """A single file diff between checkpoint and current state.

    Attributes:
        file_path: The original file path.
        status: One of 'unchanged', 'modified', 'deleted', 'added'.
        checkpoint_size: File size in the checkpoint (0 if not present).
        current_size: Current file size (0 if not present).
    """

    file_path: str
    status: str  # unchanged | modified | deleted | added
    checkpoint_size: int = 0
    current_size: int = 0


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------
class CheckpointManager:
    """Manages file-system checkpoints for SMR configuration files.

    Lifecycle: singleton, initialized at app boot.
    Thread/async safety: all mutating operations are protected by an
    ``asyncio.Lock``.
    """

    def __init__(
        self,
        state_dir: str,
        config_paths: Optional[List[str]] = None,
        max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
    ):
        """Initialize the checkpoint manager.

        Args:
            state_dir: Directory where checkpoint folders are stored.
            config_paths: Default list of file paths to snapshot.  Individual
                          ``create_checkpoint()`` calls can override this.
            max_checkpoints: Maximum number of checkpoints to retain.
                             Older ones are pruned automatically.
        """
        self.state_dir = Path(state_dir)
        self.checkpoint_dir = self.state_dir / "checkpoints"
        self.config_paths: List[str] = list(config_paths) if config_paths else []
        self.max_checkpoints = max_checkpoints
        self._lock = asyncio.Lock()

        # Ensure directories exist
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        LOG.info(
            "CheckpointManager: initialized, state_dir=%s, %d default config paths",
            self.checkpoint_dir,
            len(self.config_paths),
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def create_checkpoint(
        self,
        label: str,
        file_paths: Optional[List[str]] = None,
    ) -> Checkpoint:
        """Create a new checkpoint snapshot.

        Copies the specified files (or ``self.config_paths`` if not given)
        into a timestamped checkpoint directory.  The directory is written
        to a ``.tmp`` path first, then atomically renamed to its final name
        so partial checkpoints are never visible.

        Args:
            label: Human-readable description of why this checkpoint was
                   created (e.g. "before-fusion-strategy-update").
            file_paths: Override the default config paths.  If None, uses
                        the paths provided at init time.

        Returns:
            A ``Checkpoint`` describing the snapshot.
        """
        paths = file_paths if file_paths is not None else self.config_paths
        if not paths:
            LOG.warning("CheckpointManager: no file paths to snapshot, skipping")
            return Checkpoint(
                checkpoint_id="",
                label=label,
                created_at=time.time(),
            )

        checkpoint_id = f"{CHECKPOINT_PREFIX}{int(time.time() * 1000)}"
        cp_dir = self.checkpoint_dir / checkpoint_id
        tmp_dir = self.checkpoint_dir / f"{checkpoint_id}{TMP_SUFFIX}"

        async with self._lock:
            # Clean up stale tmp dir if it exists (e.g. from a crashed run)
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)

            files: Dict[str, str] = {}
            for fpath in paths:
                src = Path(fpath)
                if not src.exists():
                    LOG.warning("CheckpointManager: file not found, skipping: %s", fpath)
                    continue
                # Store with a flat name to avoid path traversal issues
                rel_name = src.name
                dst = tmp_dir / rel_name
                try:
                    shutil.copy2(str(src), str(dst))
                    files[str(fpath)] = str(rel_name)
                    LOG.debug("CheckpointManager: snapshotted %s -> %s", fpath, rel_name)
                except Exception as e:
                    LOG.error("CheckpointManager: failed to copy %s: %s", fpath, e)

            # Write manifest
            manifest = {
                "checkpoint_id": checkpoint_id,
                "label": label,
                "created_at": time.time(),
                "files": files,
            }
            manifest_path = tmp_dir / MANIFEST_FILENAME
            _atomic_write_json(manifest_path, manifest)

            # Atomic rename: tmp -> final
            shutil.move(str(tmp_dir), str(cp_dir))

            LOG.info(
                "CheckpointManager: created checkpoint '%s' (%d files, label='%s')",
                checkpoint_id,
                len(files),
                label,
            )

            # Prune old checkpoints
            self._prune_old_checkpoints()

            return Checkpoint(
                checkpoint_id=checkpoint_id,
                label=label,
                created_at=manifest["created_at"],
                files=files,
                file_count=len(files),
            )

    async def rollback(
        self,
        checkpoint_id: str,
        file_paths: Optional[List[str]] = None,
    ) -> RollbackResult:
        """Restore files from a checkpoint.

        Copies files from the checkpoint directory back to their original
        locations.  Only files present in the checkpoint are restored;
        files that were added after the checkpoint are left in place
        (use ``diff()`` to find them).

        Args:
            checkpoint_id: The checkpoint to restore from.
            file_paths: If provided, only restore these specific files.
                        If None, restores all files in the checkpoint.

        Returns:
            A ``RollbackResult`` describing what was restored.
        """
        cp = self._load_checkpoint(checkpoint_id)
        if cp is None:
            return RollbackResult(
                success=False,
                checkpoint_id=checkpoint_id,
                error=f"checkpoint '{checkpoint_id}' not found",
            )

        cp_dir = self.checkpoint_dir / checkpoint_id
        restored: List[str] = []
        skipped: List[str] = []

        async with self._lock:
            for orig_path, snap_name in cp.files.items():
                # Filter by file_paths if specified
                if file_paths and orig_path not in file_paths:
                    continue

                snap_file = cp_dir / snap_name
                if not snap_file.exists():
                    LOG.warning(
                        "CheckpointManager: snapshot file missing in checkpoint: %s",
                        snap_name,
                    )
                    skipped.append(orig_path)
                    continue

                try:
                    # Create parent dir if needed
                    Path(orig_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(snap_file), str(orig_path))
                    restored.append(orig_path)
                    LOG.info("CheckpointManager: restored %s", orig_path)
                except Exception as e:
                    LOG.error(
                        "CheckpointManager: failed to restore %s: %s", orig_path, e
                    )
                    skipped.append(orig_path)

        return RollbackResult(
            success=len(skipped) == 0,
            checkpoint_id=checkpoint_id,
            restored_files=restored,
            skipped_files=skipped,
        )

    def list_checkpoints(self) -> List[Checkpoint]:
        """List all available checkpoints, newest first.

        Returns:
            A list of ``Checkpoint`` objects sorted by creation time descending.
        """
        checkpoints: List[Checkpoint] = []
        if not self.checkpoint_dir.exists():
            return checkpoints

        for entry in self.checkpoint_dir.iterdir():
            if not entry.is_dir() or entry.name.endswith(TMP_SUFFIX):
                continue
            if not entry.name.startswith(CHECKPOINT_PREFIX):
                continue
            cp = self._load_checkpoint(entry.name)
            if cp:
                checkpoints.append(cp)

        # Sort by created_at descending (newest first)
        checkpoints.sort(key=lambda c: c.created_at, reverse=True)
        return checkpoints

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get details of a specific checkpoint.

        Args:
            checkpoint_id: The checkpoint ID to look up.

        Returns:
            The ``Checkpoint`` object, or None if not found.
        """
        return self._load_checkpoint(checkpoint_id)

    def diff(self, checkpoint_id: str) -> List[DiffEntry]:
        """Compare a checkpoint against the current file state.

        For each file in the checkpoint, compares the snapshot with the
        current file to detect modifications, deletions, or additions.

        Args:
            checkpoint_id: The checkpoint to compare against.

        Returns:
            A list of ``DiffEntry`` objects, one per tracked file.
        """
        cp = self._load_checkpoint(checkpoint_id)
        if cp is None:
            return []

        cp_dir = self.checkpoint_dir / checkpoint_id
        entries: List[DiffEntry] = []

        for orig_path, snap_name in cp.files.items():
            snap_file = cp_dir / snap_name
            cur_file = Path(orig_path)

            snap_size = snap_file.stat().st_size if snap_file.exists() else 0
            cur_size = cur_file.stat().st_size if cur_file.exists() else 0

            if not snap_file.exists() and not cur_file.exists():
                status = "deleted"
            elif not snap_file.exists():
                status = "added"
            elif not cur_file.exists():
                status = "deleted"
            elif _files_equal(snap_file, cur_file):
                status = "unchanged"
            else:
                status = "modified"

            entries.append(DiffEntry(
                file_path=orig_path,
                status=status,
                checkpoint_size=snap_size,
                current_size=cur_size,
            ))

        return entries

    async def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint.

        Args:
            checkpoint_id: The checkpoint to delete.

        Returns:
            True if deleted, False if not found.
        """
        cp_dir = self.checkpoint_dir / checkpoint_id
        if not cp_dir.exists():
            return False

        async with self._lock:
            shutil.rmtree(str(cp_dir), ignore_errors=True)
            LOG.info("CheckpointManager: deleted checkpoint '%s'", checkpoint_id)
        return True

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _load_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Load a checkpoint's manifest from disk.

        Args:
            checkpoint_id: The checkpoint ID.

        Returns:
            A ``Checkpoint`` object, or None if the manifest is missing or
            corrupted.
        """
        cp_dir = self.checkpoint_dir / checkpoint_id
        manifest_path = cp_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            return None

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Checkpoint(
                checkpoint_id=data["checkpoint_id"],
                label=data.get("label", ""),
                created_at=data.get("created_at", 0.0),
                files=data.get("files", {}),
                file_count=len(data.get("files", {})),
            )
        except (json.JSONDecodeError, KeyError) as e:
            LOG.error("CheckpointManager: corrupted manifest for '%s': %s", checkpoint_id, e)
            return None

    def _prune_old_checkpoints(self) -> None:
        """Remove old checkpoints beyond ``max_checkpoints``.

        Called after each ``create_checkpoint()``.  Sorts by creation time
        and deletes the oldest entries.  Not async-locked because it's
        always called from within the lock in ``create_checkpoint()``.
        """
        checkpoints = self.list_checkpoints()
        if len(checkpoints) <= self.max_checkpoints:
            return

        # list_checkpoints returns newest first, so the tail is oldest
        to_remove = checkpoints[self.max_checkpoints:]
        for cp in to_remove:
            cp_dir = self.checkpoint_dir / cp.checkpoint_id
            shutil.rmtree(str(cp_dir), ignore_errors=True)
            LOG.info("CheckpointManager: pruned old checkpoint '%s'", cp.checkpoint_id)


# ---------------------------------------------------------------------------
# module-level singleton
# ---------------------------------------------------------------------------
_manager: Optional[CheckpointManager] = None
_init_lock = asyncio.Lock()


async def init_checkpoint_manager(
    state_dir: str,
    config_paths: Optional[List[str]] = None,
    max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
) -> CheckpointManager:
    """Initialize or update the module-level CheckpointManager singleton.

    Async-safe: uses a lock so concurrent callers don't race on creation.
    """
    global _manager
    async with _init_lock:
        if _manager is None:
            _manager = CheckpointManager(
                state_dir=state_dir,
                config_paths=config_paths,
                max_checkpoints=max_checkpoints,
            )
        return _manager


def get_checkpoint_manager() -> Optional[CheckpointManager]:
    """Get the current checkpoint manager singleton (may be None if not init'd)."""
    return _manager


def reset_checkpoint_manager() -> None:
    """Reset the singleton -- primarily for testing."""
    global _manager
    _manager = None


# ---------------------------------------------------------------------------
# file utility helpers
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON to a file atomically (write to .tmp, then rename).

    Ensures that a crash mid-write never leaves a corrupted JSON file.
    """
    tmp_path = path.with_suffix(path.suffix + TMP_SUFFIX)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp_path), str(path))


def _files_equal(a: Path, b: Path) -> bool:
    """Compare two files for equality by content.

    Uses a simple chunked read.  For very large files this is O(n) but
    config files are typically small (< 1 MB).
    """
    try:
        size_a = a.stat().st_size
        size_b = b.stat().st_size
        if size_a != size_b:
            return False
        # Quick size check first, then content
        chunk_size = 8192
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca = fa.read(chunk_size)
                cb = fb.read(chunk_size)
                if ca != cb:
                    return False
                if not ca:  # Both files ended
                    return True
    except OSError:
        return False
