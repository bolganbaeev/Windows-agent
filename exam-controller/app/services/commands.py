"""Persistent, allow-listed command queue operations."""

import json
from datetime import datetime, timezone
import os

from app.database import connect

SUPPORTED_COMMANDS = frozenset({"MIGRATE_NETWORK", "OPEN_TEST"})
DEFAULT_DELIVERY_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 3


class UnknownAgentError(Exception):
    pass


class CommandNotFoundError(Exception):
    pass


class InvalidCommandStateError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def delivery_timeout_seconds() -> int:
    return _positive_env("COMMAND_DELIVERY_TIMEOUT", DEFAULT_DELIVERY_TIMEOUT)


def max_attempts() -> int:
    return _positive_env("COMMAND_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)


def create_command(agent_id: str, command_type: str, payload: dict | None = None) -> dict:
    """Create non-migration placeholder commands; migrations use migration service."""
    if command_type not in SUPPORTED_COMMANDS:
        raise ValueError("Unsupported command type")
    if command_type == "MIGRATE_NETWORK":
        raise ValueError("MIGRATE_NETWORK must be created through migration validation")
    payload = {} if payload is None else payload
    if not isinstance(payload, dict):
        raise ValueError("Command payload must be a JSON object")
    if payload:
        raise ValueError("OPEN_TEST does not accept payload fields")
    now = _now()
    with connect() as db:
        if db.execute("SELECT 1 FROM clients WHERE agent_id=?", (agent_id,)).fetchone() is None:
            raise UnknownAgentError
        return _insert_command(db, agent_id, command_type, payload, now)


def _insert_command(db, agent_id: str, command_type: str, payload: dict, now: str | None = None) -> dict:
    now = now or _now()
    cursor = db.execute(
        """INSERT INTO commands(agent_id, command_type, payload_json, status, created_at, updated_at)
           VALUES (?, ?, ?, 'PENDING', ?, ?)""",
        (agent_id, command_type, json.dumps(payload, separators=(",", ":")), now, now),
    )
    row = db.execute("SELECT * FROM commands WHERE id=?", (cursor.lastrowid,)).fetchone()
    return _decode(row)


def deliver_pending(agent_id: str) -> list[dict]:
    """Atomically claim pending or timed-out commands for exactly this agent."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    cutoff = (now_dt.timestamp() - delivery_timeout_seconds())
    cutoff_time = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    attempt_limit = max_attempts()
    with connect() as db:
        # IMMEDIATE takes SQLite's write reservation before reading eligibility. A second
        # poller cannot observe and claim the same pending/expired rows concurrently.
        db.execute("BEGIN IMMEDIATE")
        expired_migration = db.execute(
            """SELECT 1 FROM commands WHERE agent_id=? AND command_type='MIGRATE_NETWORK'
               AND status='DELIVERED' AND delivered_at<=? AND attempt_count>=? LIMIT 1""",
            (agent_id, cutoff_time, attempt_limit),
        ).fetchone()
        db.execute(
            """UPDATE commands SET status='FAILED', updated_at=?,
                      error='Maximum command delivery attempts exceeded'
               WHERE agent_id=? AND status='DELIVERED' AND delivered_at<=?
                 AND attempt_count>=?""",
            (now, agent_id, cutoff_time, attempt_limit),
        )
        if expired_migration:
            db.execute(
                "UPDATE clients SET migration_state='ERROR', migration_error=?, status='ERROR', updated_at=? "
                "WHERE agent_id=? AND migration_state IN ('PREPARING', 'MIGRATING')",
                ("Maximum command delivery attempts exceeded", now, agent_id),
            )
        db.execute(
            """UPDATE commands SET status='PENDING', updated_at=?, error=NULL
               WHERE agent_id=? AND status='DELIVERED' AND delivered_at<=?
                 AND attempt_count<?""",
            (now, agent_id, cutoff_time, attempt_limit),
        )
        rows = db.execute(
            "SELECT * FROM commands WHERE agent_id=? AND status='PENDING' AND attempt_count<? ORDER BY id",
            (agent_id, attempt_limit),
        ).fetchall()
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"UPDATE commands SET status='DELIVERED', delivered_at=?, updated_at=?, "
                f"attempt_count=attempt_count+1 WHERE status='PENDING' AND id IN ({placeholders}) "
                f"AND attempt_count<?",
                [now, now, *ids, attempt_limit],
            )
            delivered = db.execute(
                f"SELECT * FROM commands WHERE id IN ({placeholders}) ORDER BY id", ids
            ).fetchall()
            return [_decode(row) for row in delivered]
        return []


def acknowledge(agent_id: str, command_id: int, *, status: str,
                result: str | None = None, error: str | None = None) -> dict:
    if status not in {"ACKNOWLEDGED", "FAILED"}:
        raise ValueError("Acknowledgement status must be ACKNOWLEDGED or FAILED")
    now = _now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM commands WHERE id=? AND agent_id=?",
                         (command_id, agent_id)).fetchone()
        if row is None:
            raise CommandNotFoundError
        if row["status"] != "DELIVERED":
            # A response may be lost after the transaction commits. Repeating the
            # exact same terminal acknowledgement is safe and returns the same row.
            if row["status"] == status:
                same_result = status != "ACKNOWLEDGED" or row["result"] == result
                same_error = status != "FAILED" or row["error"] == error
                if same_result and same_error:
                    return _decode(row)
            raise InvalidCommandStateError
        changed = db.execute(
            """UPDATE commands SET status=?, updated_at=?, acknowledged_at=?, result=?, error=?
               WHERE id=? AND agent_id=? AND status='DELIVERED'""",
            (status, now, now, result if status == "ACKNOWLEDGED" else None,
             error if status == "FAILED" else None, command_id, agent_id),
        ).rowcount
        if changed != 1:
            raise InvalidCommandStateError
        updated = db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
        if updated["command_type"] == "MIGRATE_NETWORK" and status == "FAILED":
            db.execute(
                """UPDATE clients SET migration_state='ERROR', migration_error=?, status='ERROR',
                          updated_at=? WHERE agent_id=?""",
                (error or "Migration command failed", now, agent_id),
            )
        return _decode(updated)


def history(agent_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM commands WHERE agent_id=? ORDER BY id", (agent_id,)).fetchall()
    return [_decode(row) for row in rows]


def _decode(row) -> dict:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result
