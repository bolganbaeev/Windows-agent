import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import connect, database_path
from app.main import app
from app.services import commands, migration


TOKEN = {"X-Agent-Token": "migration-test-token"}
VALID_TARGET = {"target_ip": "192.168.1.101", "prefix_length": 24,
                "gateway": "192.168.1.1"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "migration.sqlite3"))
    monkeypatch.setenv("AGENT_TOKEN", "migration-test-token")
    monkeypatch.setenv("ENABLE_TEST_COMMAND_API", "true")
    monkeypatch.setenv("INITIAL_NETWORK", "192.168.0.0/24")
    monkeypatch.setenv("INITIAL_GATEWAY", "192.168.0.1")
    monkeypatch.setenv("INITIAL_CONTROLLER_URL", "http://192.168.0.10:8000")
    monkeypatch.setenv("INTERNAL_NETWORK", "192.168.1.0/24")
    monkeypatch.setenv("INTERNAL_GATEWAY", "192.168.1.1")
    monkeypatch.setenv("INTERNAL_CONTROLLER_URL", "http://192.168.1.10:8000")
    with TestClient(app) as client:
        yield client


def register(api, hostname="PC-01", ip="192.168.0.101"):
    agent_id = str(uuid4())
    response = api.post("/api/agents/register", headers=TOKEN, json={
        "agent_id": agent_id, "hostname": hostname,
        "ip_addresses": [ip], "agent_version": "0.1.0",
    })
    assert response.status_code == 200
    return agent_id


def preflight(api, agent_id, target=None):
    return api.post(f"/api/agents/{agent_id}/migration/preflight", headers=TOKEN,
                    json=target or VALID_TARGET)


def migration_command(api, agent_id, target=None):
    return api.post(f"/api/agents/{agent_id}/commands/test", headers=TOKEN, json={
        "command_type": "MIGRATE_NETWORK", "payload": target or VALID_TARGET,
    })


def test_valid_preflight_is_structured_and_read_only(api):
    agent_id = register(api)
    result = preflight(api, agent_id)
    assert result.status_code == 200
    body = result.json()
    assert body["allowed"] is True
    assert body["agent_id"] == agent_id
    assert body["target_ip"] == "192.168.1.101"
    assert body["prefix_length"] == 24
    assert body["gateway"] == "192.168.1.1"
    assert body["internal_controller_url"] == "http://192.168.1.10:8000"
    assert body["initial_controller_url"] == "http://192.168.0.10:8000"
    assert body["errors"] == []
    with connect() as db:
        row = db.execute("SELECT target_ip, migration_state FROM clients WHERE agent_id=?",
                         (agent_id,)).fetchone()
        count = db.execute("SELECT count(*) FROM commands WHERE agent_id=?", (agent_id,)).fetchone()[0]
    assert row["target_ip"] is None
    assert row["migration_state"] == "IDLE"
    assert count == 0


@pytest.mark.parametrize("target,expected", [
    ({"target_ip": "bad-ip", "prefix_length": 24, "gateway": "192.168.1.1"}, "valid IPv4"),
    ({"target_ip": "192.168.0.101", "prefix_length": 24, "gateway": "192.168.1.1"}, "INTERNAL_NETWORK"),
    ({"target_ip": "192.168.2.101", "prefix_length": 24, "gateway": "192.168.1.1"}, "INTERNAL_NETWORK"),
    ({"target_ip": "192.168.1.0", "prefix_length": 24, "gateway": "192.168.1.1"}, "network address"),
    ({"target_ip": "192.168.1.255", "prefix_length": 24, "gateway": "192.168.1.1"}, "broadcast address"),
    ({"target_ip": "192.168.1.1", "prefix_length": 24, "gateway": "192.168.1.1"}, "INTERNAL_GATEWAY"),
    ({"target_ip": "192.168.1.10", "prefix_length": 24, "gateway": "192.168.1.1"}, "Controller IP"),
    ({"target_ip": "192.168.1.101", "prefix_length": 25, "gateway": "192.168.1.1"}, "prefix_length"),
    ({"target_ip": "192.168.1.101", "prefix_length": 24, "gateway": "192.168.1.254"}, "gateway must equal"),
])
def test_invalid_target_preflight_reports_errors(api, target, expected):
    agent_id = register(api)
    response = preflight(api, agent_id, target)
    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert any(expected.lower() in error.lower() for error in response.json()["errors"])
    # Preflight never persists target, state or validation error.
    with connect() as db:
        row = db.execute("SELECT target_ip, migration_state, migration_error FROM clients WHERE agent_id=?",
                         (agent_id,)).fetchone()
    assert row["target_ip"] is None
    assert row["migration_state"] == "IDLE"
    assert row["migration_error"] is None


def test_command_creation_reserves_ip_and_keeps_agent_online(api):
    agent_id = register(api)
    response = migration_command(api, agent_id)
    assert response.status_code == 200
    command = response.json()
    assert command["command_type"] == "MIGRATE_NETWORK"
    assert command["payload"] == VALID_TARGET
    assert command["status"] == "PENDING"
    with connect() as db:
        row = db.execute("""SELECT status, target_ip, target_gateway, migration_state,
                           migration_requested_at, migration_started_at
                           FROM clients WHERE agent_id=?""", (agent_id,)).fetchone()
    assert row["status"] == "ONLINE"
    assert row["target_ip"] == "192.168.1.101"
    assert row["target_gateway"] == "192.168.1.1"
    assert row["migration_state"] == "PREPARING"
    assert row["migration_requested_at"]
    assert row["migration_started_at"] is None


def test_internal_migration_execution_hook_transitions_to_migrating(api):
    agent_id = register(api)
    command = migration_command(api, agent_id).json()
    with pytest.raises(ValueError, match="must be delivered"):
        migration.mark_migration_started(agent_id, command["id"])
    api.get(f"/api/agents/{agent_id}/commands", headers=TOKEN)
    migration.mark_migration_started(agent_id, command["id"])
    with connect() as db:
        row = db.execute("SELECT status, migration_state, migration_started_at FROM clients WHERE agent_id=?",
                         (agent_id,)).fetchone()
    assert row["status"] == "MIGRATING"
    assert row["migration_state"] == "MIGRATING"
    assert row["migration_started_at"]


def test_target_reservation_conflict_and_same_agent_inspection(api):
    first = register(api, "PC-01")
    second = register(api, "PC-02", "192.168.0.102")
    assert migration_command(api, first).status_code == 200
    conflict = preflight(api, second)
    assert conflict.json()["allowed"] is False
    assert any("already assigned" in error for error in conflict.json()["errors"])
    assert migration_command(api, second).status_code == 422
    # The partial unique index protects assignments even if application checks are bypassed.
    with pytest.raises(sqlite3.IntegrityError):
        with connect() as db:
            db.execute("UPDATE clients SET target_ip='192.168.1.101' WHERE agent_id=?", (second,))
    same_agent = preflight(api, first)
    assert same_agent.json()["allowed"] is True
    assert "already reserved" in same_agent.json()["warnings"][0]
    with connect() as db:
        count = db.execute("SELECT count(*) FROM commands WHERE agent_id=?", (first,)).fetchone()[0]
    assert count == 1


def test_target_cannot_match_another_agents_current_ip(api):
    owner = register(api, "PC-01", "192.168.0.101")
    register(api, "PC-02", "192.168.1.150")
    target = {"target_ip": "192.168.1.150", "prefix_length": 24,
              "gateway": "192.168.1.1"}
    result = preflight(api, owner, target).json()
    assert result["allowed"] is False
    assert any("already assigned" in error for error in result["errors"])


def test_unknown_agent_and_conflicting_active_migration_are_rejected(api):
    unknown = str(uuid4())
    assert preflight(api, unknown).json()["allowed"] is False
    assert migration_command(api, unknown).status_code == 404
    agent_id = register(api)
    assert migration_command(api, agent_id).status_code == 200
    different_target = {"target_ip": "192.168.1.102", "prefix_length": 24,
                        "gateway": "192.168.1.1"}
    assert preflight(api, agent_id, different_target).json()["allowed"] is False
    assert migration_command(api, agent_id, different_target).status_code == 422
    with connect() as db:
        count = db.execute("SELECT count(*) FROM commands WHERE agent_id=?", (agent_id,)).fetchone()[0]
    assert count == 1


def test_invalid_creation_persists_migration_error_but_no_command(api):
    agent_id = register(api)
    invalid = {"target_ip": "192.168.2.8", "prefix_length": 24, "gateway": "192.168.1.1"}
    response = migration_command(api, agent_id, invalid)
    assert response.status_code == 422
    with connect() as db:
        row = db.execute("SELECT status, migration_state, migration_error FROM clients WHERE agent_id=?",
                         (agent_id,)).fetchone()
        count = db.execute("SELECT count(*) FROM commands WHERE agent_id=?", (agent_id,)).fetchone()[0]
    assert row["status"] == "ONLINE"
    assert row["migration_state"] == "ERROR"
    assert "INTERNAL_NETWORK" in row["migration_error"]
    assert count == 0


def test_failed_migration_ack_persists_error_state(api):
    agent_id = register(api)
    command = migration_command(api, agent_id).json()
    api.get(f"/api/agents/{agent_id}/commands", headers=TOKEN)
    response = api.post(f"/api/agents/{agent_id}/commands/{command['id']}/ack", headers=TOKEN,
                        json={"status": "FAILED", "error": "mock failure"})
    assert response.status_code == 200
    with connect() as db:
        row = db.execute("SELECT migration_state, migration_error FROM clients WHERE agent_id=?",
                         (agent_id,)).fetchone()
    assert row["migration_state"] == "ERROR"
    assert row["migration_error"] == "mock failure"


def test_test_command_route_cannot_bypass_migration_validation(api):
    agent_id = register(api)
    missing = api.post(f"/api/agents/{agent_id}/commands/test", headers=TOKEN,
                       json={"command_type": "MIGRATE_NETWORK"})
    extra_field = api.post(f"/api/agents/{agent_id}/commands/test", headers=TOKEN, json={
        "command_type": "MIGRATE_NETWORK", "payload": {**VALID_TARGET, "shell": "ignored"},
    })
    direct = None
    with pytest.raises(ValueError):
        direct = commands.create_command(agent_id, "MIGRATE_NETWORK", VALID_TARGET)
    assert direct is None
    assert missing.status_code == 422
    assert extra_field.status_code == 422
    assert commands.history(agent_id) == []
