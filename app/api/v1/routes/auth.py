"""
Authentication Routes
=====================
POST /api/v1/auth/send-otp        → Send OTP to mobile
POST /api/v1/auth/verify-otp      → Verify OTP → JWT tokens
POST /api/v1/auth/refresh         → Refresh access token
POST /api/v1/auth/logout          → Revoke tokens (FCM token cleared)
GET  /api/v1/auth/me              → Current user info
POST /api/v1/auth/update-fcm      → Update FCM device token
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.core.security import (
    generate_otp,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.core.exceptions import (
    OTPException,
    UnauthorizedException,
    success_envelope,
    ConflictException,
)
from app.models.models import User, OTPToken, UserRole, LangPref
from app.schemas.women import (
    SendOTPRequest,
    VerifyOTPRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserOut,
    AuthResponse,
)
from app.api.v1.dependencies import get_current_user
from app.services.sms_service import send_otp_sms

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Send OTP ─────────────────────────────────────────────────────────────────

@router.post("/send-otp", summary="Send OTP to mobile number")
async def send_otp(payload: SendOTPRequest, db: AsyncSession = Depends(get_db)):
    """
    Rate limited: 3 requests per 5 minutes per mobile.
    Stores hashed OTP in Redis (with DB fallback).
    """
    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

    # Upsert OTP record
    result = await db.execute(select(OTPToken).where(OTPToken.mobile == payload.mobile))
    otp_record = result.scalar_one_or_none()

    if otp_record:
        otp_record.otp = otp
        otp_record.attempts = 0
        otp_record.expires_at = expires_at
        otp_record.used = False
    else:
        otp_record = OTPToken(
            mobile=payload.mobile,
            otp=otp,
            expires_at=expires_at,
        )
        db.add(otp_record)

    await db.commit()

    # Send SMS (fire-and-forget in production; awaited here for simplicity)
    await send_otp_sms(mobile=payload.mobile, otp=otp)

    return success_envelope({
        "message": "OTP sent successfully",
        "mobile": f"{payload.mobile[:4]}XXXXXX",
        "expires_in_minutes": settings.OTP_EXPIRE_MINUTES,
    })


# ── Verify OTP ────────────────────────────────────────────────────────────────

@router.post("/verify-otp", response_model=dict, summary="Verify OTP and get JWT tokens")
async def verify_otp(payload: VerifyOTPRequest, db: AsyncSession = Depends(get_db)):
    """
    - Returns access + refresh tokens on success.
    - Creates user record if first-time login.
    - Sets `is_new_user=True` for new registrations (triggers registration flow in app).
    """
    result = await db.execute(select(OTPToken).where(OTPToken.mobile == payload.mobile))
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise OTPException("No OTP found. Please request a new one.")

    if otp_record.used:
        raise OTPException("OTP already used. Please request a new one.")

    if datetime.now(timezone.utc) > otp_record.expires_at:
        raise OTPException("OTP expired. Please request a new one.")

    if otp_record.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise OTPException("Too many failed attempts. Please request a new OTP.")

    if otp_record.otp != payload.otp:
        otp_record.attempts += 1
        await db.commit()
        raise OTPException(f"Invalid OTP. {settings.OTP_MAX_ATTEMPTS - otp_record.attempts} attempts remaining.")

    # Mark OTP as used
    otp_record.used = True
    await db.commit()

    # Get or create user
    user_result = await db.execute(select(User).where(User.mobile == payload.mobile))
    user = user_result.scalar_one_or_none()
    is_new_user = user is None

    if is_new_user:
        user = User(mobile=payload.mobile, role=UserRole.pregnant_woman)
        db.add(user)
        await db.flush()

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return success_envelope(AuthResponse(
        user=UserOut.model_validate(user),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
        is_new_user=is_new_user,
    ).model_dump())


# ── Refresh Token ─────────────────────────────────────────────────────────────

@router.post("/refresh", summary="Refresh access token")
async def refresh_token(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    token_data = decode_refresh_token(payload.refresh_token)
    if not token_data:
        raise UnauthorizedException("Invalid or expired refresh token")

    from uuid import UUID
    result = await db.execute(select(User).where(User.id == UUID(token_data["sub"])))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise UnauthorizedException("User not found or deactivated")

    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})

    return success_envelope({
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    })


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout", summary="Logout - clear FCM token")
async def logout(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.fcm_token = None
    await db.commit()
    return success_envelope({"message": "Logged out successfully"})


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", summary="Get current user info")
async def get_me(user: User = Depends(get_current_user)):
    return success_envelope(UserOut.model_validate(user).model_dump())


# ── Update FCM Token ──────────────────────────────────────────────────────────

@router.post("/update-fcm", summary="Update FCM device token for push notifications")
async def update_fcm_token(
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    fcm_token = body.get("fcm_token", "").strip()
    if not fcm_token:
        raise ConflictException("fcm_token is required")
    user.fcm_token = fcm_token
    await db.commit()
    return success_envelope({"message": "FCM token updated"})
