import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DB_PATH

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                player_id TEXT,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                palette TEXT,
                size INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT,
                result_path TEXT
            )
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        for name, ddl in (
            ("player_id", "TEXT"),
            ("stage", "TEXT"),
            ("steps", "INTEGER DEFAULT 0"),
            ("tool", "TEXT"),
            ("started_at", "TEXT"),
        ):
            if name not in cols:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(task_id: str, player_id: Optional[str], prompt: str, palette: str, size: str) -> None:
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, player_id, status, prompt, palette, size, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, player_id, "queued", prompt, palette, size, now, now),
        )


def update_task(task_id: str, status: str, error: Optional[str] = None,
                result_path: Optional[str] = None) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, error=?, result_path=?, updated_at=? WHERE id=?",
            (status, error, result_path, _now(), task_id),
        )


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def update_progress(task_id: str, stage: Optional[str] = None, steps: Optional[int] = None,
                    tool: Optional[str] = None, started_at: Optional[str] = None) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    if stage is not None:
        sets.append("stage=?")
        vals.append(stage)
    if steps is not None:
        sets.append("steps=?")
        vals.append(int(steps))
    if tool is not None:
        sets.append("tool=?")
        vals.append(tool)
    if started_at is not None:
        sets.append("started_at=?")
        vals.append(started_at)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(task_id)
    with _lock, _connect() as conn:
        conn.execute("UPDATE tasks SET " + ", ".join(sets) + " WHERE id=?", vals)