"""Persistence and liveness rules for registered agents."""

from datetime import datetime, timedelta, timezone
import ipaddress
import sqlite3

from app.database import connect
from app.config import network_settings

OFFLINE_AFTER = timedelta(seconds=15)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def primary_ipv4(addresses: list[str]) -> str | None:
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.version == 4 and not parsed.is_loopback:
            return str(parsed)
    return None


def network_for_ip(address: str | None) -> str | None:
    if not address:
        return None
    try:
        ip = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return None
    settings = network_settings()
    if ip in settings.initial_network:
        return str(settings.initial_network)
    if ip in settings.internal_network:
        return str(settings.internal_network)
    return None


def register(*, agent_id: str, hostname: str, ip_addresses: list[str], version: str) -> dict:
    now = serialize_time(utc_now())
    ip = primary_ipv4(ip_addresses)
    network = network_for_ip(ip)
    with connect() as db:
        db.execute(
            """INSERT INTO clients
               (agent_id, hostname, current_ip, network, status, last_seen, version, updated_at)
               VALUES (?, ?, ?, ?, 'ONLINE', ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 hostname=excluded.hostname, current_ip=excluded.current_ip,
                 network=excluded.network,
                 status=CASE WHEN clients.migration_state IN ('MIGRATING', 'MIGRATED', 'ERROR')
                             THEN clients.migration_state ELSE 'ONLINE' END,
                 last_seen=excluded.last_seen,
                 version=excluded.version, error=NULL, updated_at=excluded.updated_at""",
            (agent_id, hostname, ip, network, now, version, now),
        )
        row = db.execute("SELECT * FROM clients WHERE agent_id = ?", (agent_id,)).fetchone()
        return dict(row)


def heartbeat(*, agent_id: str, hostname: str, ip_addresses: list[str]) -> dict | None:
    now = serialize_time(utc_now())
    ip = primary_ipv4(ip_addresses)
    network = network_for_ip(ip)
    with connect() as db:
        changed = db.execute(
            """UPDATE clients SET hostname=?, current_ip=?, network=?, last_seen=?,
                      status=CASE WHEN migration_state IN ('MIGRATING', 'MIGRATED', 'ERROR')
                                  THEN migration_state ELSE 'ONLINE' END,
                      error=NULL, updated_at=? WHERE agent_id=?""",
            (hostname, ip, network, now, now, agent_id),
        ).rowcount
        if not changed:
            return None
        return dict(db.execute("SELECT * FROM clients WHERE agent_id=?", (agent_id,)).fetchone())


def get_all() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM clients ORDER BY hostname COLLATE NOCASE, agent_id").fetchall()
    return [_with_live_status(dict(row)) for row in rows]


def get_one(agent_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM clients WHERE agent_id=?", (agent_id,)).fetchone()
    return _with_live_status(dict(row)) if row else None


def _with_live_status(agent: dict) -> dict:
    last_seen = agent.get("last_seen")
    if last_seen:
        parsed = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        if utc_now() - parsed > OFFLINE_AFTER:
            agent["status"] = "OFFLINE"
            with connect() as db:
                db.execute("UPDATE clients SET status='OFFLINE' WHERE agent_id=?", (agent["agent_id"],))
    return agent
