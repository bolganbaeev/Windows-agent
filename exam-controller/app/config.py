"""Explicit Controller network topology settings."""

import os
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

APP_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Load the simple KEY=VALUE entries used by the project's .env file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(APP_ROOT / ".env")


@dataclass(frozen=True)
class NetworkSettings:
    initial_network: IPv4Network
    initial_gateway: IPv4Address
    initial_controller_url: str
    internal_network: IPv4Network
    internal_gateway: IPv4Address
    internal_controller_url: str
    internal_controller_ip: IPv4Address | None


def network_settings() -> NetworkSettings:
    initial_network = ip_network(os.getenv("INITIAL_NETWORK", "192.168.0.0/24"), strict=False)
    internal_network = ip_network(os.getenv("INTERNAL_NETWORK", "192.168.1.0/24"), strict=False)
    if not isinstance(initial_network, IPv4Network) or not isinstance(internal_network, IPv4Network):
        raise ValueError("INITIAL_NETWORK and INTERNAL_NETWORK must be IPv4 networks")
    initial_gateway = ip_address(os.getenv("INITIAL_GATEWAY", "192.168.0.1"))
    internal_gateway = ip_address(os.getenv("INTERNAL_GATEWAY", "192.168.1.1"))
    if not isinstance(initial_gateway, IPv4Address) or not isinstance(internal_gateway, IPv4Address):
        raise ValueError("INITIAL_GATEWAY and INTERNAL_GATEWAY must be IPv4 addresses")
    if initial_gateway not in initial_network:
        raise ValueError("INITIAL_GATEWAY must be inside INITIAL_NETWORK")
    if internal_network.prefixlen != 24:
        raise ValueError("Phase 5A supports only a /24 INTERNAL_NETWORK")
    if internal_gateway not in internal_network or internal_gateway in {
        internal_network.network_address, internal_network.broadcast_address,
    }:
        raise ValueError("INTERNAL_GATEWAY must be a usable address inside INTERNAL_NETWORK")
    internal_url = os.getenv("INTERNAL_CONTROLLER_URL", "http://192.168.1.10:8000")
    parsed_internal_url = urlparse(internal_url)
    if parsed_internal_url.scheme not in {"http", "https"} or not parsed_internal_url.hostname:
        raise ValueError("INTERNAL_CONTROLLER_URL must be an HTTP(S) URL with a hostname")
    initial_url = os.getenv("INITIAL_CONTROLLER_URL", "http://192.168.0.10:8000")
    parsed_initial_url = urlparse(initial_url)
    if parsed_initial_url.scheme not in {"http", "https"} or not parsed_initial_url.hostname:
        raise ValueError("INITIAL_CONTROLLER_URL must be an HTTP(S) URL with a hostname")
    host = os.getenv("INTERNAL_CONTROLLER_IP", parsed_internal_url.hostname or "")
    try:
        controller_ip = ip_address(host) if host else None
        if controller_ip is not None and not isinstance(controller_ip, IPv4Address):
            if os.getenv("INTERNAL_CONTROLLER_IP"):
                raise ValueError("INTERNAL_CONTROLLER_IP must be an IPv4 address")
            controller_ip = None
    except ValueError:
        if os.getenv("INTERNAL_CONTROLLER_IP"):
            raise ValueError("INTERNAL_CONTROLLER_IP must be a valid IPv4 address") from None
        controller_ip = None
    return NetworkSettings(
        initial_network=initial_network,
        initial_gateway=initial_gateway,
        initial_controller_url=initial_url,
        internal_network=internal_network,
        internal_gateway=internal_gateway,
        internal_controller_url=internal_url,
        internal_controller_ip=controller_ip,
    )
