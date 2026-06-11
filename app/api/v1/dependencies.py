from typing import Optional
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_access_token
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.models.models import User, UserRole


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedException("Authorization header missing or malformed")

    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)

    if not payload or "sub" not in payload:
        raise UnauthorizedException("Invalid or expired access token")

    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise UnauthorizedException("User not found or deactivated")

    return user


async def get_current_woman(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.pregnant_woman:
        raise ForbiddenException("Access restricted to registered women only")
    return user


async def get_current_field_worker(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.asha, UserRole.anm):
        raise ForbiddenException("Access restricted to ASHA/ANM workers only")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.block_admin, UserRole.pi, UserRole.super_admin):
        raise ForbiddenException("Access restricted to admin users only")
    return user


async def get_any_authenticated(user: User = Depends(get_current_user)) -> User:
    """Any authenticated user (women, field worker, admin)."""
    return user
 
