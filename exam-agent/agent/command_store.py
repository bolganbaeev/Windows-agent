"""Small persistent idempotency ledger for command IDs handled by this Agent."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ProcessedCommandStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS processed_commands (
                    command_id TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PROCESSING', 'ACKNOWLEDGED', 'FAILED')),
                    result TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def claim(self, command_id: str, command_type: str) -> tuple[str, dict | None]:
        """Reserve a new ID once or return its saved terminal outcome."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM processed_commands WHERE command_id=?",
                             (command_id,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO processed_commands(command_id, command_type, status, updated_at) "
                    "VALUES (?, ?, 'PROCESSING', ?)",
                    (command_id, command_type, self._now()),
                )
                return "NEW", None
            if row["command_type"] != command_type:
                return "CONFLICT", {"status": "FAILED", "error": "command ID type mismatch"}
            if row["status"] == "PROCESSING":
                # A previous process stopped after claiming this ID. Never run it again.
                message = "previous processing was interrupted; command was not repeated"
                db.execute(
                    "UPDATE processed_commands SET status='FAILED', error=?, updated_at=? "
                    "WHERE command_id=? AND status='PROCESSING'",
                    (message, self._now(), command_id),
                )
                return "INTERRUPTED", {"status": "FAILED", "error": message}
            return row["status"], {
                "status": row["status"], "result": row["result"], "error": row["error"],
            }

    def complete(self, command_id: str, outcome: dict) -> None:
        status = outcome["status"]
        if status not in {"ACKNOWLEDGED", "FAILED"}:
            raise ValueError("Outcome must be terminal")
        with self._connect() as db:
            changed = db.execute(
                """UPDATE processed_commands SET status=?, result=?, error=?, updated_at=?
                   WHERE command_id=? AND status='PROCESSING'""",
                (status, outcome.get("result") if status == "ACKNOWLEDGED" else None,
                 outcome.get("error") if status == "FAILED" else None,
                 self._now(), command_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Command ID was not in PROCESSING state")


def outcome_for_saved_record(state: str, outcome: dict) -> dict:
    """Remove irrelevant nullable result/error fields from the local ledger result."""
    if state in {"ACKNOWLEDGED", "FAILED"}:
        return {key: value for key, value in outcome.items() if value is not None}
    return outcome
