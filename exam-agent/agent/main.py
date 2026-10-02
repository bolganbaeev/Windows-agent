"""Run ExamAgent in the foreground for initial lab testing."""

import logging
import socket
import time

from agent import client, config
from agent.command_store import ProcessedCommandStore, outcome_for_saved_record

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("exam-agent")


def local_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127."):
                found.add(address)
    except OSError as exc:
        log.warning("Could not enumerate host IPv4 addresses: %s", exc)
    # A UDP route lookup discovers the interface Windows would use to reach the Controller;
    # connect() on a UDP socket sends no packet.
    try:
        host = config.CONTROLLER_URL.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 80))
            address = sock.getsockname()[0]
            if not address.startswith("127."):
                found.add(address)
    except OSError:
        pass
    return sorted(found)


def payload(agent_id: str, *, registering: bool) -> dict:
    data = {
        "agent_id": agent_id,
        "hostname": socket.gethostname(),
        "ip_addresses": local_ipv4_addresses(),
    }
    if registering:
        data["agent_version"] = config.AGENT_VERSION
    return data


def handle_command(command: dict) -> dict:
    """Mock-only allow-listed handler. Never interprets command payload fields."""
    command_id = command.get("id")
    command_type = command.get("command_type")
    if command_type == "MIGRATE_NETWORK":
        log.info("Received MIGRATE_NETWORK command %s", command_id)
        return {"status": "ACKNOWLEDGED", "result": "mock migration completed"}
    if command_type == "OPEN_TEST":
        log.info("Received OPEN_TEST command %s", command_id)
        return {"status": "ACKNOWLEDGED", "result": "mock test launch completed"}
    log.error("Received unsupported command type for command %s", command_id)
    return {"status": "FAILED", "error": "unsupported command type"}


def process_command(command: dict, store: ProcessedCommandStore) -> dict:
    """Run a mock handler at most once per persistent command ID."""
    command_id = str(command.get("id", ""))
    command_type = command.get("command_type", "")
    state, saved = store.claim(command_id, command_type)
    if state != "NEW":
        log.info("Command %s was already processed or claimed; reusing saved outcome", command_id)
        return outcome_for_saved_record(state, saved or {"status": "FAILED", "error": "duplicate command"})
    try:
        outcome = handle_command(command)
    except Exception:
        log.exception("Mock handler raised for command %s", command_id)
        outcome = {"status": "FAILED", "error": "mock handler error"}
    store.complete(command_id, outcome)
    return outcome


def poll_and_process_commands(agent_id: str, store: ProcessedCommandStore | None = None) -> None:
    store = store or ProcessedCommandStore(config.COMMAND_DB_PATH)
    try:
        received = client.get_commands(agent_id)
    except client.ControllerError as exc:
        log.warning("Command polling failed; will retry: %s", exc)
        return
    for command in received:
        try:
            acknowledgement = process_command(command, store)
            client.acknowledge(agent_id, command["id"], acknowledgement)
        except (client.ControllerError, KeyError, TypeError) as exc:
            log.warning("Could not acknowledge command %s; will continue: %s",
                        command.get("id", "?"), exc)
        except Exception:
            log.exception("Command handling failed for command %s", command.get("id", "?"))
            try:
                client.acknowledge(agent_id, command["id"], {
                    "status": "FAILED", "error": "mock handler error",
                })
            except Exception:
                log.exception("Could not report command failure")


def run() -> None:
    agent_id = config.load_agent_id()
    log.info("Starting ExamAgent agent_id=%s controller=%s", agent_id, config.CONTROLLER_URL)
    registered = False
    while True:
        try:
            if not registered:
                client.register(payload(agent_id, registering=True))
                registered = True
                log.info("Registered with Controller")
            else:
                client.heartbeat(payload(agent_id, registering=False))
                log.debug("Heartbeat sent")
            poll_and_process_commands(agent_id)
        except client.ControllerError as exc:
            registered = False
            log.warning("Controller connection failed; retrying: %s", exc)
        except Exception:
            # An unexpected local reporting issue should not kill a classroom agent loop.
            log.exception("Unexpected reporting error; retrying")
        time.sleep(config.HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("ExamAgent stopped")
