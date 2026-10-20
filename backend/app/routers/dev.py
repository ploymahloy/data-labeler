"""Local-development login. Only mounted when AUTH_MODE=dev; never in production."""

from fastapi import APIRouter, Depends

from app.auth import mint_dev_token
from app.config import Settings, get_settings
from app.schemas import DevLoginRequest, DevLoginResponse

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/login", response_model=DevLoginResponse)
async def dev_login(
    body: DevLoginRequest, settings: Settings = Depends(get_settings)
) -> DevLoginResponse:
    token = mint_dev_token(
        settings, body.email, list(body.roles), ttl_seconds=body.ttl_seconds or 8 * 3600
    )
    return DevLoginResponse(token=token, sub=f"dev-{body.email}")
