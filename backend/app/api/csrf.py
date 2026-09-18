from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import validate_csrf
from app.core.config import get_settings
from app.core.security import make_csrf_token

router = APIRouter(tags=["csrf"])


@router.get("/csrf")
async def csrf(response: Response, _: Annotated[None, Depends(validate_csrf)] = None) -> dict:
    """Set the double-submit CSRF cookie. Frontend reads it and echoes it back
    as X-CSRF-Token on every state-changing request."""
    settings = get_settings()
    token = make_csrf_token(settings.csrf_secret)
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain,
        path="/",
    )
    return {"csrfToken": token}