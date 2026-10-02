import os
from fastapi import APIRouter, Header, HTTPException

from app.models import (AgentHeartbeat, AgentRegistration, AgentResponse,
                        CommandAcknowledgement, CommandResponse, TestCommandRequest)
from app.services import agents, commands
from app.api.auth import require_agent_token, validate_agent_id
from app.services import migration

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/register", response_model=AgentResponse)
def register_agent(body: AgentRegistration, x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    agent_id = validate_agent_id(body.agent_id)
    row = agents.register(agent_id=agent_id, hostname=body.hostname,
                          ip_addresses=body.ip_addresses, version=body.agent_version)
    return _response(row)


@router.post("/heartbeat", response_model=AgentResponse)
def heartbeat(body: AgentHeartbeat, x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    agent_id = validate_agent_id(body.agent_id)
    row = agents.heartbeat(agent_id=agent_id, hostname=body.hostname,
                           ip_addresses=body.ip_addresses)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent is not registered")
    return _response(row)


@router.get("", response_model=list[AgentResponse])
def list_agents():
    return [_response(row) for row in agents.get_all()]


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str):
    normalized = validate_agent_id(agent_id)
    row = agents.get_one(normalized)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _response(row)


@router.post("/{agent_id}/commands/test", response_model=CommandResponse)
def create_test_command(agent_id: str, body: TestCommandRequest,
                        x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    if os.getenv("ENABLE_TEST_COMMAND_API", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="Test command endpoint is disabled")
    normalized = validate_agent_id(agent_id)
    try:
        if body.command_type == "MIGRATE_NETWORK":
            return migration.create_migration_command(normalized, body.payload.model_dump())
        return commands.create_command(normalized, body.command_type)
    except commands.UnknownAgentError:
        raise HTTPException(status_code=404, detail="Agent not found") from None
    except migration.MigrationValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/{agent_id}/commands", response_model=list[CommandResponse])
def poll_commands(agent_id: str, x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    normalized = validate_agent_id(agent_id)
    if agents.get_one(normalized) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return commands.deliver_pending(normalized)


@router.post("/{agent_id}/commands/{command_id}/ack", response_model=CommandResponse)
def acknowledge_command(agent_id: str, command_id: int, body: CommandAcknowledgement,
                        x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    normalized = validate_agent_id(agent_id)
    try:
        return commands.acknowledge(normalized, command_id, status=body.status,
                                    result=body.result, error=body.error)
    except commands.CommandNotFoundError:
        raise HTTPException(status_code=404, detail="Command not found for this agent") from None
    except commands.InvalidCommandStateError:
        raise HTTPException(status_code=409, detail="Command is not awaiting acknowledgement") from None


def _response(row: dict) -> dict:
    row["last_seen"] = row.get("last_seen")
    return row
