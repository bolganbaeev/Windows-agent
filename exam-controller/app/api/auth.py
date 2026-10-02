import os
from uuid import UUID

from fastapi import Header, HTTPException


def require_agent_token(x_agent_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("AGENT_TOKEN", "dev-agent-token-change-me")
    if x_agent_token != expected:
        raise HTTPException(status_code=401, detail="Invalid agent token")


def validate_agent_id(agent_id: str) -> str:
    try:
        return str(UUID(agent_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="agent_id must be a UUID") from None
