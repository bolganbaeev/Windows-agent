from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import commands


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "commands.sqlite3"))
    monkeypatch.setenv("AGENT_TOKEN", "queue-test-token")
    monkeypatch.setenv("ENABLE_TEST_COMMAND_API", "true")
    with TestClient(app) as client:
        yield client


def register(api, hostname="PC-01"):
    agent_id = str(uuid4())
    response = api.post("/api/agents/register", headers={"X-Agent-Token": "queue-test-token"}, json={
        "agent_id": agent_id,
        "hostname": hostname,
        "ip_addresses": ["192.168.0.101"],
        "agent_version": "0.1.0",
    })
    assert response.status_code == 200
    return agent_id


def create(api, agent_id, command_type="MIGRATE_NETWORK"):
    body = {"command_type": command_type}
    if command_type == "MIGRATE_NETWORK":
        body["payload"] = {
            "target_ip": "192.168.1.101", "prefix_length": 24, "gateway": "192.168.1.1",
        }
    return api.post(f"/api/agents/{agent_id}/commands/test",
                    headers={"X-Agent-Token": "queue-test-token"},
                    json=body)


def poll(api, agent_id):
    return api.get(f"/api/agents/{agent_id}/commands",
                   headers={"X-Agent-Token": "queue-test-token"})


def ack(api, agent_id, command_id, body):
    return api.post(f"/api/agents/{agent_id}/commands/{command_id}/ack",
                    headers={"X-Agent-Token": "queue-test-token"}, json=body)


def test_command_creation_and_unknown_type_rejection(api):
    agent_id = register(api)
    response = create(api, agent_id)
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert response.json()["payload"] == {
        "target_ip": "192.168.1.101", "prefix_length": 24, "gateway": "192.168.1.1",
    }
    with pytest.raises(ValueError):
        commands.create_command(agent_id, "RUN_SHELL", {"command": "unsafe"})
    rejected = create(api, agent_id, "RUN_SHELL")
    assert rejected.status_code == 422


def test_fetch_marks_delivered_and_does_not_redeliver(api):
    agent_id = register(api)
    created = create(api, agent_id).json()
    fetched = poll(api, agent_id)
    assert fetched.status_code == 200
    assert [item["id"] for item in fetched.json()] == [created["id"]]
    assert fetched.json()[0]["status"] == "DELIVERED"
    assert fetched.json()[0]["attempt_count"] == 1
    assert fetched.json()[0]["delivered_at"]
    assert poll(api, agent_id).json() == []


def test_agent_cannot_fetch_another_agents_command(api):
    owner_id = register(api, "PC-01")
    other_id = register(api, "PC-02")
    command_id = create(api, owner_id).json()["id"]
    assert poll(api, other_id).json() == []
    assert poll(api, owner_id).json()[0]["id"] == command_id


def test_acknowledged_command_is_persisted_in_history(api):
    agent_id = register(api)
    command_id = create(api, agent_id).json()["id"]
    assert poll(api, agent_id).status_code == 200
    response = ack(api, agent_id, command_id,
                   {"status": "ACKNOWLEDGED", "result": "mock completed"})
    assert response.status_code == 200
    assert response.json()["status"] == "ACKNOWLEDGED"
    assert response.json()["attempt_count"] == 1
    assert response.json()["acknowledged_at"]
    history = commands.history(agent_id)
    assert len(history) == 1
    assert history[0]["id"] == command_id
    assert history[0]["result"] == "mock completed"
    repeated = ack(api, agent_id, command_id,
                   {"status": "ACKNOWLEDGED", "result": "mock completed"})
    assert repeated.status_code == 200
    assert len(commands.history(agent_id)) == 1


def test_failed_acknowledgement_and_invalid_status(api):
    agent_id = register(api)
    command_id = create(api, agent_id, "OPEN_TEST").json()["id"]
    poll(api, agent_id)
    invalid = ack(api, agent_id, command_id, {"status": "PENDING"})
    assert invalid.status_code == 422
    failed = ack(api, agent_id, command_id,
                 {"status": "FAILED", "error": "mock failure"})
    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error"] == "mock failure"


def test_unregistered_agent_cannot_create_command(api):
    response = create(api, str(uuid4()))
    assert response.status_code == 404


def test_ack_must_belong_to_agent_and_be_delivered(api):
    owner_id = register(api, "PC-01")
    other_id = register(api, "PC-02")
    command_id = create(api, owner_id).json()["id"]
    assert ack(api, other_id, command_id, {"status": "ACKNOWLEDGED"}).status_code == 404
    assert ack(api, owner_id, command_id, {"status": "ACKNOWLEDGED"}).status_code == 409
