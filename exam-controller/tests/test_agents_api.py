from datetime import datetime, timedelta, timezone
import os
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import database_path
from app.main import app


class AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_database_path = os.environ.get("DATABASE_PATH")
        self.old_token = os.environ.get("AGENT_TOKEN")
        os.environ["DATABASE_PATH"] = os.path.join(self.temp_dir.name, "controller.sqlite3")
        os.environ["AGENT_TOKEN"] = "test-token"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.agent_id = str(uuid4())
        self.headers = {"X-Agent-Token": "test-token"}
        self.registration = {
            "agent_id": self.agent_id,
            "hostname": "PC-01",
            "ip_addresses": ["192.168.0.101"],
            "agent_version": "0.1.0",
        }

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        if self.old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.old_database_path
        if self.old_token is None:
            os.environ.pop("AGENT_TOKEN", None)
        else:
            os.environ["AGENT_TOKEN"] = self.old_token
        self.temp_dir.cleanup()

    def test_registration_upsert_heartbeat_and_offline_detection(self):
        response = self.client.post("/api/agents/register", json=self.registration, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ONLINE")
        self.assertEqual(response.json()["current_ip"], "192.168.0.101")

        # Re-registering after an Agent restart updates the existing UUID row.
        self.registration["hostname"] = "PC-01-renamed"
        self.assertEqual(self.client.post("/api/agents/register", json=self.registration,
                                          headers=self.headers).status_code, 200)
        rows = self.client.get("/api/agents").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hostname"], "PC-01-renamed")

        heartbeat = {"agent_id": self.agent_id, "hostname": "PC-01",
                     "ip_addresses": ["192.168.0.102"]}
        response = self.client.post("/api/agents/heartbeat", json=heartbeat, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_ip"], "192.168.0.102")
        self.assertEqual(response.json()["status"], "ONLINE")
        self.assertEqual(self.client.get(f"/api/agents/{self.agent_id}").status_code, 200)

        old = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(database_path()) as db:
            db.execute("UPDATE clients SET last_seen=? WHERE agent_id=?", (old, self.agent_id))
        rows = self.client.get("/api/agents").json()
        self.assertEqual(rows[0]["status"], "OFFLINE")

        response = self.client.post("/api/agents/heartbeat", json=heartbeat, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ONLINE")

    def test_registration_requires_development_token(self):
        response = self.client.post("/api/agents/register", json=self.registration)
        self.assertEqual(response.status_code, 401)

    def test_dashboard_renders_headers_and_empty_state(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for header in ("Hostname", "Agent ID", "IP Address", "Network", "Status",
                       "Last Seen", "Agent Version"):
            self.assertIn(header, response.text)
        self.assertIn("No agents registered.", response.text)

    def test_dashboard_shows_agent_and_offline_status(self):
        registered = self.client.post("/api/agents/register", json=self.registration,
                                      headers=self.headers)
        self.assertEqual(registered.status_code, 200)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PC-01", response.text)
        self.assertIn("192.168.0.101", response.text)
        self.assertIn("ONLINE", response.text)

        old = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(database_path()) as db:
            db.execute("UPDATE clients SET last_seen=? WHERE agent_id=?", (old, self.agent_id))
        api_agents = self.client.get("/api/agents").json()
        self.assertEqual(api_agents[0]["status"], "OFFLINE")
        dashboard = self.client.get("/")
        self.assertIn("OFFLINE", dashboard.text)


if __name__ == "__main__":
    unittest.main()
