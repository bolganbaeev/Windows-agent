from fastapi import APIRouter, Header

from app.api.auth import require_agent_token, validate_agent_id
from app.models import MigrationPreflightResponse, MigrationTarget
from app.services import migration

router = APIRouter(prefix="/agents", tags=["migration"])


@router.post("/{agent_id}/migration/preflight", response_model=MigrationPreflightResponse)
def migration_preflight(agent_id: str, body: MigrationTarget,
                        x_agent_token: str | None = Header(default=None)):
    require_agent_token(x_agent_token)
    normalized = validate_agent_id(agent_id)
    return migration.preflight(normalized, body.model_dump())
