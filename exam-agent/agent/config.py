"""Environment configuration and persistent local agent identity."""

import os
from pathlib import Path
from uuid import uuid4

CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://192.168.0.10:8000").rstrip("/")
INTERNAL_CONTROLLER_URL = os.getenv("INTERNAL_CONTROLLER_URL", "http://192.168.1.10:8000").rstrip("/")
HEARTBEAT_INTERVAL = max(1, int(os.getenv("HEARTBEAT_INTERVAL", "5")))
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "dev-agent-token-change-me")
AGENT_VERSION = "0.1.0"
IDENTITY_FILE = Path(os.getenv("AGENT_ID_FILE", str(Path(__file__).resolve().parent.parent / "agent_id")))
COMMAND_DB_PATH = Path(os.getenv(
    "AGENT_COMMAND_DB", str(Path(__file__).resolve().parent.parent / "processed_commands.sqlite3")
))


def load_agent_id() -> str:
    IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    configured = os.getenv("AGENT_ID", "").strip()
    if configured:
        IDENTITY_FILE.write_text(configured + "\n", encoding="utf-8")
        return configured
    try:
        value = IDENTITY_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = str(uuid4())
    IDENTITY_FILE.write_text(value + "\n", encoding="utf-8")
    return value
