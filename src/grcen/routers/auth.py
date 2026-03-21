import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from grcen.routers.deps import get_db
from grcen.schemas.user import UserCreate, UserResponse
from grcen.services.auth import authenticate_user
from grcen.services import audit_service as audit_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(
    data: UserCreate,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
):
    user = await authenticate_user(pool, data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["user_id"] = str(user.id)
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
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}
