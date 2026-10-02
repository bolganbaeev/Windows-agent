"""SQLite connection and schema initialization for ExamController."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def database_path() -> Path:
    """Return the configured SQLite path, creating its parent directory."""
    path = Path(os.getenv("DATABASE_PATH", "./data/exam-controller.sqlite3"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    """Create the durable client registry schema if it does not exist."""
    with connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT NOT NULL,
                agent_id TEXT NOT NULL UNIQUE,
                current_ip TEXT,
                status TEXT NOT NULL DEFAULT 'OFFLINE'
                    CHECK (status IN ('ONLINE', 'OFFLINE', 'MIGRATING', 'MIGRATED', 'ERROR')),
                last_seen TEXT,
                network TEXT,
                target_ip TEXT,
                target_gateway TEXT,
                version TEXT,
                error TEXT,
                migration_state TEXT NOT NULL DEFAULT 'IDLE'
                    CHECK (migration_state IN ('IDLE', 'PREPARING', 'MIGRATING', 'MIGRATED', 'ERROR')),
                migration_error TEXT,
                migration_requested_at TEXT,
                migration_started_at TEXT,
                migration_completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL REFERENCES clients(agent_id),
                command_type TEXT NOT NULL
                    CHECK (command_type IN ('MIGRATE_NETWORK', 'OPEN_TEST')),
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'DELIVERED', 'ACKNOWLEDGED', 'FAILED', 'CANCELLED')),
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivered_at TEXT,
                acknowledged_at TEXT,
                result TEXT,
                error TEXT
            )
            """
        )
        # Upgrade command tables created before attempt tracking was introduced.
        command_columns = {row["name"] for row in connection.execute("PRAGMA table_info(commands)")}
        if "attempt_count" not in command_columns:
            connection.execute(
                "ALTER TABLE commands ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
            )
        client_columns = {row["name"] for row in connection.execute("PRAGMA table_info(clients)")}
        migration_columns = {
            "migration_state": "TEXT NOT NULL DEFAULT 'IDLE' CHECK (migration_state IN ('IDLE', 'PREPARING', 'MIGRATING', 'MIGRATED', 'ERROR'))",
            "migration_error": "TEXT",
            "migration_requested_at": "TEXT",
            "migration_started_at": "TEXT",
            "migration_completed_at": "TEXT",
        }
        for name, definition in migration_columns.items():
            if name not in client_columns:
                connection.execute(f"ALTER TABLE clients ADD COLUMN {name} {definition}")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_target_ip_unique "
            "ON clients(target_ip) WHERE target_ip IS NOT NULL"
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_target_ip_not_other_current
               BEFORE UPDATE OF target_ip ON clients
               WHEN NEW.target_ip IS NOT NULL AND EXISTS (
                   SELECT 1 FROM clients WHERE agent_id<>NEW.agent_id AND current_ip=NEW.target_ip
               )
               BEGIN SELECT RAISE(ABORT, 'target IP is assigned as another client current IP'); END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_insert_target_ip_not_other_current
               BEFORE INSERT ON clients
               WHEN NEW.target_ip IS NOT NULL AND EXISTS (
                   SELECT 1 FROM clients WHERE current_ip=NEW.target_ip
               )
               BEGIN SELECT RAISE(ABORT, 'target IP is assigned as another client current IP'); END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_current_ip_not_other_target
               BEFORE UPDATE OF current_ip ON clients
               WHEN NEW.current_ip IS NOT NULL AND EXISTS (
                   SELECT 1 FROM clients WHERE agent_id<>NEW.agent_id AND target_ip=NEW.current_ip
               )
               BEGIN SELECT RAISE(ABORT, 'current IP is reserved for another client'); END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_insert_current_ip_not_other_target
               BEFORE INSERT ON clients
               WHEN NEW.current_ip IS NOT NULL AND EXISTS (
                   SELECT 1 FROM clients WHERE target_ip=NEW.current_ip
               )
               BEGIN SELECT RAISE(ABORT, 'current IP is reserved for another client'); END"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_commands_agent_status ON commands(agent_id, status, id)"
        )
