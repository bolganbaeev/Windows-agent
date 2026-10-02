import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient

AGENT_ROOT = Path(__file__).resolve().parents[2] / "exam-agent"
sys.path.insert(0, str(AGENT_ROOT))

from agent import client
from agent import main
from agent.command_store import ProcessedCommandStore

from app.database import database_path
from app.main import app
from app.services import commands


def test_mock_migrate_command_only_acknowledges(caplog):
    caplog.set_level("INFO")
    response = main.handle_command({"id": 11, "command_type": "MIGRATE_NETWORK",
                                    "payload": {"ignore_me": "never execute"}})
    assert response == {"status": "ACKNOWLEDGED", "result": "mock migration completed"}
    assert "Received MIGRATE_NETWORK command 11" in caplog.text


def test_mock_open_test_only_acknowledges(caplog):
    caplog.set_level("INFO")
    response = main.handle_command({"id": 12, "command_type": "OPEN_TEST",
                                    "payload": {"url": "http://untrusted.invalid"}})
    assert response == {"status": "ACKNOWLEDGED", "result": "mock test launch completed"}
    assert "Received OPEN_TEST command 12" in caplog.text


def test_unknown_command_is_failed_without_execution():
    response = main.handle_command({"id": 13, "command_type": "RUN_SHELL",
                                    "payload": {"command": "unsafe"}})
    assert response == {"status": "FAILED", "error": "unsupported command type"}


def test_agent_polls_and_acknowledges_mock_commands(monkeypatch):
    received = [
        {"id": 21, "command_type": "MIGRATE_NETWORK", "payload": {}},
        {"id": 22, "command_type": "OPEN_TEST", "payload": {}},
    ]
    acknowledgements = []
    monkeypatch.setattr(client, "get_commands", lambda _agent_id: received)
    monkeypatch.setattr(client, "acknowledge",
                        lambda agent_id, command_id, body: acknowledgements.append(
                            (agent_id, command_id, body)))
    main.poll_and_process_commands("persistent-agent-id")
    assert [item[1] for item in acknowledgements] == [21, 22]
    assert [item[2]["status"] for item in acknowledgements] == ["ACKNOWLEDGED", "ACKNOWLEDGED"]


def test_agent_continues_polling_after_controller_api_failure(monkeypatch):
    class StopLoop(BaseException):
        pass

    monkeypatch.setattr(main.config, "load_agent_id", lambda: "persistent-agent-id")
    monkeypatch.setattr(main, "payload", lambda *args, **kwargs: {})
    register_calls = []
    heartbeat_calls = []
    poll_calls = []
    monkeypatch.setattr(client, "register", lambda payload: register_calls.append(payload) or {})
    monkeypatch.setattr(client, "heartbeat", lambda payload: heartbeat_calls.append(payload) or {})

    def get_commands(_agent_id):
        poll_calls.append(1)
        if len(poll_calls) == 1:
            raise client.ControllerError("temporary API failure")
        return []

    monkeypatch.setattr(client, "get_commands", get_commands)

    def stop_after_retry(_interval):
        if len(poll_calls) >= 2:
            raise StopLoop()

    monkeypatch.setattr(main.time, "sleep", stop_after_retry)
    try:
        main.run()
    except StopLoop:
        pass
    assert len(register_calls) == 1
    assert len(heartbeat_calls) == 1
    assert len(poll_calls) == 2


def test_agent_restart_recovers_same_command_without_reprocessing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "controller.sqlite3"))
    monkeypatch.setenv("AGENT_TOKEN", "restart-token")
    monkeypatch.setenv("ENABLE_TEST_COMMAND_API", "true")
    monkeypatch.setenv("COMMAND_DELIVERY_TIMEOUT", "2")
    monkeypatch.setenv("COMMAND_MAX_ATTEMPTS", "3")
    token = {"X-Agent-Token": "restart-token"}
    ledger_path = tmp_path / "agent-commands.sqlite3"
    with TestClient(app) as controller:
        agent_id = str(uuid4())
        registered = controller.post("/api/agents/register", headers=token, json={
            "agent_id": agent_id, "hostname": "PC-RESTART",
            "ip_addresses": ["192.168.0.101"], "agent_version": "0.1.0",
        })
        assert registered.status_code == 200
        created = controller.post(f"/api/agents/{agent_id}/commands/test", headers=token,
                                  json={"command_type": "MIGRATE_NETWORK", "payload": {
                                      "target_ip": "192.168.1.101", "prefix_length": 24,
                                      "gateway": "192.168.1.1",
                                  }}).json()
        first_delivery = controller.get(f"/api/agents/{agent_id}/commands", headers=token).json()[0]
        assert first_delivery["status"] == "DELIVERED"

        calls = []
        real_handler = main.handle_command

        def counted_handler(command):
            calls.append(command["id"])
            return real_handler(command)

        monkeypatch.setattr(main, "handle_command", counted_handler)
        first_outcome = main.process_command(first_delivery, ProcessedCommandStore(ledger_path))
        # Simulate the Agent stopping after handling the command but before sending ACK.
        expired = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(database_path()) as db:
            db.execute("UPDATE commands SET delivered_at=? WHERE id=?", (expired, created["id"]))

        redelivery = controller.get(f"/api/agents/{agent_id}/commands", headers=token).json()[0]
        assert redelivery["id"] == created["id"]
        assert redelivery["attempt_count"] == 2
        # A new store instance represents a restarted Agent process reading the same ledger.
        recovered_outcome = main.process_command(redelivery, ProcessedCommandStore(ledger_path))
        assert recovered_outcome == first_outcome
        assert calls == [created["id"]]

        acked = controller.post(
            f"/api/agents/{agent_id}/commands/{created['id']}/ack",
            headers=token, json=recovered_outcome,
        )
        assert acked.status_code == 200
        history = commands.history(agent_id)
        assert len(history) == 1
        assert history[0]["id"] == created["id"]
        assert history[0]["status"] == "ACKNOWLEDGED"
