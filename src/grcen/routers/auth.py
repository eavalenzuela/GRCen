import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from grcen.config import settings
from grcen.rate_limit import check_login_rate_limit
from grcen.routers.deps import get_db
from grcen.schemas.user import UserCreate, UserResponse
from grcen.services.auth import (
    authenticate_user,
    check_lockout,
    record_failed_login,
    record_successful_login,
)
from grcen.services import audit_service as audit_svc
from grcen.services import session_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", dependencies=[Depends(check_login_rate_limit)])
async def login(
    data: UserCreate,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
):
    # Check lockout before attempting authentication
    if await check_lockout(pool, data.username):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    user = await authenticate_user(pool, data.username, data.password)
    if not user:
        await record_failed_login(
            pool, data.username,
            settings.LOGIN_MAX_FAILED_ATTEMPTS,
            settings.LOGIN_LOCKOUT_MINUTES,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await record_successful_login(pool, user.id)

    # Session fixation prevention: clear old session data before creating new session
    request.session.clear()
    session_id = await session_service.create_session(
        pool,
        user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    request.session["session_id"] = session_id

    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="login",
        entity_type="user",
        entity_id=user.id,
        entity_name=user.username,
    )
    return UserResponse.model_validate(user, from_attributes=True)


@router.post("/logout")
async def logout(request: Request, pool: asyncpg.Pool = Depends(get_db)):
    session_id = request.session.get("session_id")
    if session_id:
        await session_service.invalidate_session(pool, session_id)
    request.session.clear()
    return {"ok": True}
