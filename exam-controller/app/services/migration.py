"""Migration target preflight and intent persistence; no network changes are performed."""

from datetime import datetime, timezone
from ipaddress import IPv4Address

from app.config import NetworkSettings, network_settings
from app.database import connect
from app.services import commands


class MigrationValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate(db, agent_id: str, payload: dict, settings: NetworkSettings,
              *, for_creation: bool) -> tuple[list[str], list[str], dict | None]:
    errors: list[str] = []
    warnings: list[str] = []
    client = db.execute(
        "SELECT agent_id, current_ip, target_ip, migration_state FROM clients WHERE agent_id=?",
        (agent_id,),
    ).fetchone()
    if client is None:
        return ["agent_id is not registered"], warnings, None

    target_ip = payload.get("target_ip", "")
    target = None
    try:
        target = IPv4Address(target_ip)
    except (ValueError, TypeError):
        errors.append("target_ip must be a valid IPv4 address")

    if payload.get("prefix_length") != 24:
        errors.append("prefix_length must be 24")
    if payload.get("gateway") != str(settings.internal_gateway):
        errors.append(f"gateway must equal INTERNAL_GATEWAY ({settings.internal_gateway})")

    if target is not None:
        if target not in settings.internal_network:
            errors.append(f"target_ip must belong to INTERNAL_NETWORK ({settings.internal_network})")
        if target == settings.internal_network.network_address:
            errors.append("target_ip cannot be the INTERNAL_NETWORK address")
        if target == settings.internal_network.broadcast_address:
            errors.append("target_ip cannot be the INTERNAL_NETWORK broadcast address")
        if target == settings.internal_gateway:
            errors.append("target_ip cannot equal INTERNAL_GATEWAY")
        if settings.internal_controller_ip is not None and target == settings.internal_controller_ip:
            errors.append("target_ip cannot equal the internal Controller IP")
        owner = db.execute(
            """SELECT agent_id FROM clients
               WHERE agent_id<>? AND (target_ip=? OR current_ip=?) LIMIT 1""",
            (agent_id, str(target), str(target)),
        ).fetchone()
        if owner is not None:
            errors.append("target_ip is already assigned to another Agent")

    active_command = db.execute(
        """SELECT 1 FROM commands WHERE agent_id=? AND command_type='MIGRATE_NETWORK'
           AND status IN ('PENDING', 'DELIVERED') LIMIT 1""",
        (agent_id,),
    ).fetchone()
    intent_active = client["migration_state"] in {"PREPARING", "MIGRATING"}
    same_existing_reservation = target is not None and client["target_ip"] == str(target)
    if active_command or intent_active:
        if for_creation or not same_existing_reservation:
            errors.append("Agent already has an active migration intent")
        elif same_existing_reservation:
            warnings.append("target_ip is already reserved for this Agent")

    if client["current_ip"]:
        try:
            current = IPv4Address(client["current_ip"])
            if current not in settings.initial_network and current not in settings.internal_network:
                warnings.append("Agent current IP is outside the configured lab networks")
        except ValueError:
            warnings.append("Agent current IP is not a valid IPv4 address")
    return errors, warnings, client


def preflight(agent_id: str, payload: dict) -> dict:
    """Read-only validation. This method does not reserve an address or change state."""
    settings = network_settings()
    with connect() as db:
        errors, warnings, _ = _validate(db, agent_id, payload, settings, for_creation=False)
    return {
        "allowed": not errors,
        "agent_id": agent_id,
        "target_ip": payload.get("target_ip", ""),
        "gateway": payload.get("gateway", ""),
        "prefix_length": payload.get("prefix_length", 0),
        "initial_network": str(settings.initial_network),
        "initial_gateway": str(settings.initial_gateway),
        "initial_controller_url": settings.initial_controller_url,
        "internal_network": str(settings.internal_network),
        "internal_gateway": str(settings.internal_gateway),
        "internal_controller_url": settings.internal_controller_url,
        "warnings": warnings,
        "errors": errors,
    }


def create_migration_command(agent_id: str, payload: dict) -> dict:
    """Validate, reserve target IP and create the structured command atomically."""
    settings = network_settings()
    errors: list[str]
    command: dict | None = None
    unknown_agent = False
    now = _now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        errors, _warnings, client = _validate(db, agent_id, payload, settings, for_creation=True)
        unknown_agent = client is None
        if errors:
            if client is not None:
                active = client["migration_state"] in {"PREPARING", "MIGRATING"}
                db.execute(
                    "UPDATE clients SET migration_state=?, migration_error=?, updated_at=? WHERE agent_id=?",
                    ("PREPARING" if active else "ERROR", "; ".join(errors), now, agent_id),
                )
        else:
            db.execute(
                """UPDATE clients SET status='ONLINE', target_ip=?, target_gateway=?, migration_state='PREPARING',
                          migration_error=NULL, migration_requested_at=?, migration_started_at=NULL,
                          migration_completed_at=NULL, updated_at=? WHERE agent_id=?""",
                (str(IPv4Address(payload["target_ip"])), str(settings.internal_gateway), now, now, agent_id),
            )
            normalized = {
                "target_ip": str(IPv4Address(payload["target_ip"])),
                "prefix_length": 24,
                "gateway": str(settings.internal_gateway),
            }
            command = commands._insert_command(db, agent_id, "MIGRATE_NETWORK", normalized, now)
    if errors:
        if unknown_agent:
            raise commands.UnknownAgentError
        raise MigrationValidationError(errors)
    return command


def mark_migration_started(agent_id: str, command_id: int) -> None:
    """Future execution hook: mark intent as running without applying networking."""
    now = _now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        command = db.execute(
            "SELECT status FROM commands WHERE id=? AND agent_id=? AND command_type='MIGRATE_NETWORK'",
            (command_id, agent_id),
        ).fetchone()
        if command is None:
            raise ValueError("Migration command not found for Agent")
        if command["status"] != "DELIVERED":
            raise ValueError("Migration command must be delivered before execution starts")
        changed = db.execute(
            """UPDATE clients SET migration_state='MIGRATING', status='MIGRATING',
                      migration_started_at=?, migration_error=NULL, updated_at=?
               WHERE agent_id=? AND migration_state='PREPARING'""",
            (now, now, agent_id),
        ).rowcount
        if changed != 1:
            raise ValueError("Agent has no migration intent in PREPARING state")
