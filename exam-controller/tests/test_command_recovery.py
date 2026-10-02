from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import database_path, initialize_database
from app.main import app
from app.services import commands


@pytest.fixture
def recovery_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "recovery.sqlite3"))
    monkeypatch.setenv("AGENT_TOKEN", "recovery-token")
    monkeypatch.setenv("ENABLE_TEST_COMMAND_API", "true")
    monkeypatch.setenv("COMMAND_DELIVERY_TIMEOUT", "2")
    monkeypatch.setenv("COMMAND_MAX_ATTEMPTS", "3")
    with TestClient(app) as client:
        yield client


def add_agent(api):
    agent_id = str(uuid4())
    response = api.post("/api/agents/register", headers={"X-Agent-Token": "recovery-token"}, json={
        "agent_id": agent_id, "hostname": "PC-RECOVERY",
        "ip_addresses": ["192.168.0.101"], "agent_version": "0.1.0",
    })
    assert response.status_code == 200
    return agent_id


def create_command(api, agent_id):
    response = api.post(f"/api/agents/{agent_id}/commands/test",
                        headers={"X-Agent-Token": "recovery-token"},
                        json={"command_type": "MIGRATE_NETWORK", "payload": {
                            "target_ip": "192.168.1.101", "prefix_length": 24,
                            "gateway": "192.168.1.1",
                        }})
    assert response.status_code == 200
    return response.json()


def poll(api, agent_id):
    return api.get(f"/api/agents/{agent_id}/commands",
                   headers={"X-Agent-Token": "recovery-token"})


def expire_delivery(command_id):
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(database_path()) as db:
        db.execute("UPDATE commands SET delivered_at=? WHERE id=?", (old, command_id))


def test_redelivery_uses_same_id_and_increments_attempts(recovery_api):
    agent_id = add_agent(recovery_api)
    created = create_command(recovery_api, agent_id)
    assert created["status"] == "PENDING"
    assert created["attempt_count"] == 0

    first = poll(recovery_api, agent_id).json()[0]
    assert first["id"] == created["id"]
    assert first["status"] == "DELIVERED"
    assert first["attempt_count"] == 1

    expire_delivery(first["id"])
    second = poll(recovery_api, agent_id).json()[0]
    assert second["id"] == first["id"]
    assert second["status"] == "DELIVERED"
    assert second["attempt_count"] == 2


def test_timeout_then_acknowledgement_keeps_attempt_count(recovery_api):
    agent_id = add_agent(recovery_api)
    command_id = create_command(recovery_api, agent_id)["id"]
    poll(recovery_api, agent_id)
    expire_delivery(command_id)
    redelivered = poll(recovery_api, agent_id).json()[0]
    response = recovery_api.post(
        f"/api/agents/{agent_id}/commands/{command_id}/ack",
        headers={"X-Agent-Token": "recovery-token"},
        json={"status": "ACKNOWLEDGED", "result": "mock completed"},
    )
    assert redelivered["attempt_count"] == 2
    assert response.json()["status"] == "ACKNOWLEDGED"
    assert response.json()["attempt_count"] == 2


def test_command_fails_after_max_attempts_and_is_not_delivered_again(recovery_api):
    agent_id = add_agent(recovery_api)
    command_id = create_command(recovery_api, agent_id)["id"]
    assert poll(recovery_api, agent_id).json()[0]["attempt_count"] == 1
    expire_delivery(command_id)
    assert poll(recovery_api, agent_id).json()[0]["attempt_count"] == 2
    expire_delivery(command_id)
    assert poll(recovery_api, agent_id).json()[0]["attempt_count"] == 3
    expire_delivery(command_id)
    assert poll(recovery_api, agent_id).json() == []
    row = commands.history(agent_id)[0]
    assert row["id"] == command_id
    assert row["status"] == "FAILED"
    assert row["error"] == "Maximum command delivery attempts exceeded"
    with sqlite3.connect(database_path()) as db:
        agent_state = db.execute("SELECT status, migration_state, migration_error FROM clients WHERE agent_id=?",
                                 (agent_id,)).fetchone()
    assert agent_state == ("ERROR", "ERROR", "Maximum command delivery attempts exceeded")
    expire_delivery(command_id)
    assert poll(recovery_api, agent_id).json() == []


def test_two_simultaneous_polls_claim_command_only_once(recovery_api):
    agent_id = add_agent(recovery_api)
    created = create_command(recovery_api, agent_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commands.deliver_pending(agent_id), range(2)))
    returned = [command for batch in results for command in batch]
    assert len(returned) == 1
    assert returned[0]["id"] == created["id"]
    assert returned[0]["attempt_count"] == 1


def test_existing_phase4_database_gets_attempt_count_column(tmp_path, monkeypatch):
    path = tmp_path / "old-controller.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE clients (agent_id TEXT NOT NULL UNIQUE, target_ip TEXT)")
        db.execute("INSERT INTO clients(agent_id, target_ip) VALUES ('old-agent', NULL)")
        db.execute("""CREATE TABLE commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL REFERENCES clients(agent_id),
            command_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            delivered_at TEXT, acknowledged_at TEXT, result TEXT, error TEXT
        )""")
        db.execute("""INSERT INTO commands(agent_id, command_type, created_at, updated_at)
                      VALUES ('old-agent', 'OPEN_TEST', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")
    initialize_database()
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(commands)")}
        attempt_count = db.execute("SELECT attempt_count FROM commands WHERE id=1").fetchone()[0]
    assert "attempt_count" in columns
    assert attempt_count == 0
