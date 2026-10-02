"""HTTP models for the Controller API."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class HealthResponse(BaseModel):
    status: str
    service: str
    api_version: str


class AgentRegistration(BaseModel):
    agent_id: str
    hostname: str = Field(min_length=1, max_length=255)
    ip_addresses: list[str] = Field(default_factory=list)
    agent_version: str = Field(min_length=1, max_length=40)


class AgentHeartbeat(BaseModel):
    agent_id: str
    hostname: str = Field(min_length=1, max_length=255)
    ip_addresses: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    hostname: str
    agent_id: str
    current_ip: str | None
    status: str
    last_seen: datetime | None
    network: str | None = None
    target_ip: str | None = None
    target_gateway: str | None = None
    version: str | None
    migration_state: str = "IDLE"
    migration_error: str | None = None
    migration_requested_at: datetime | None = None
    migration_started_at: datetime | None = None
    migration_completed_at: datetime | None = None


class MigrationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_ip: StrictStr
    prefix_length: StrictInt
    gateway: StrictStr


class MigrationPreflightResponse(BaseModel):
    allowed: bool
    agent_id: str
    target_ip: str
    gateway: str
    prefix_length: int
    initial_network: str
    initial_gateway: str
    initial_controller_url: str
    internal_network: str
    internal_gateway: str
    internal_controller_url: str
    warnings: list[str]
    errors: list[str]


class TestCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_type: Literal["MIGRATE_NETWORK", "OPEN_TEST"]
    payload: MigrationTarget | None = None

    @model_validator(mode="after")
    def enforce_command_payload(self):
        if self.command_type == "MIGRATE_NETWORK" and self.payload is None:
            raise ValueError("MIGRATE_NETWORK requires a validated target payload")
        if self.command_type == "OPEN_TEST" and self.payload is not None:
            raise ValueError("OPEN_TEST does not accept a payload")
        return self


class CommandResponse(BaseModel):
    id: int
    agent_id: str
    command_type: Literal["MIGRATE_NETWORK", "OPEN_TEST"]
    payload: dict
    status: Literal["PENDING", "DELIVERED", "ACKNOWLEDGED", "FAILED", "CANCELLED"]
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    result: str | None
    error: str | None


class CommandAcknowledgement(BaseModel):
    status: Literal["ACKNOWLEDGED", "FAILED"]
    result: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)
