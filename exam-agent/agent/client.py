"""Small HTTP client for the registration and heartbeat endpoints."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent import config


class ControllerError(Exception):
    pass


def post(path: str, payload: dict) -> dict:
    request = Request(
        config.CONTROLLER_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Agent-Token": config.AGENT_TOKEN},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ControllerError(str(exc)) from exc


def register(payload: dict) -> dict:
    return post("/api/agents/register", payload)


def heartbeat(payload: dict) -> dict:
    return post("/api/agents/heartbeat", payload)


def get_commands(agent_id: str) -> list[dict]:
    request = Request(
        f"{config.CONTROLLER_URL}/api/agents/{agent_id}/commands",
        headers={"Accept": "application/json", "X-Agent-Token": config.AGENT_TOKEN},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, list):
            raise ValueError("Controller command response must be a list")
        return result
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        raise ControllerError(str(exc)) from exc


def acknowledge(agent_id: str, command_id: int, payload: dict) -> dict:
    return post(f"/api/agents/{agent_id}/commands/{command_id}/ack", payload)
