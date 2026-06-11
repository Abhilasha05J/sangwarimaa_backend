"""
Admin / Program Manager Routes  — All 8 panels from wireframe
=============================================================
Auth (email+password based — no OTP):
POST   /api/v1/admin/login                      → Email + password login

A. Overview Panel
GET    /api/v1/admin/overview                   → Total users, active, drop-off, avg BPCR, alerts, OKRs

B. BPCR Analytics
GET    /api/v1/admin/bpcr/distribution          → Score distribution Red/Yellow/Green
GET    /api/v1/admin/bpcr/trend                 → BPCR score trend over time
GET    /api/v1/admin/bpcr/block-comparison      → Block-wise BPCR comparison

C. Alert Monitoring
GET    /api/v1/admin/alerts                     → All alerts with filters
GET    /api/v1/admin/alerts/{id}                → Alert detail

D. Response Time Tracking
GET    /api/v1/admin/response-time              → Avg response, % within SLA, delayed, by block

E. User Engagement
GET    /api/v1/admin/engagement                 → DAU, reminders sent, most used module

F. GIS / Map
GET    /api/v1/admin/gis/high-risk              → High risk beneficiary coordinates
GET    /api/v1/admin/gis/alerts                 → Alert location coordinates

G. Data Export
GET    /api/v1/admin/export/bpcr                → BPCR data as CSV
GET    /api/v1/admin/export/users               → User data as CSV
GET    /api/v1/admin/export/alerts              → Alert logs as CSV

H. Settings
GET    /api/v1/admin/users                      → List all admin/ASHA/ANM users
POST   /api/v1/admin/users                      → Create block admin / BMO user
PATCH  /api/v1/admin/users/{id}                 → Update user (role, active status)
DELETE /api/v1/admin/users/{id}                 → Deactivate user
GET    /api/v1/admin/settings                   → Get system settings
PATCH  /api/v1/admin/settings                   → Update alert threshold, language

I. Update/Manage Health facility list 
GET  /api/v1/admin/facilities           → List all facilities
POST /api/v1/admin/facilities           → Add a new facility
PATCH /api/v1/admin/facilities/{id}     → Update facility
DELETE /api/v1/admin/facilities/{id}    → Deactivate facilit
"""

import csv
import io
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_

from app.core.database import get_db
from app.core.config import settings
from app.core.security import verify_password, hash_password, create_access_token, create_refresh_token
from app.core.exceptions import (
    NotFoundException, ConflictException,
    UnauthorizedException, ForbiddenException,
    success_envelope,
)
from app.models.models import (
    User, FieldWorker, Beneficiary, Alert, ANCVisit,
    BPCRAssessment, Reminder, AdminCredential,
    AlertStatus, AlertType, RiskLevel, UserRole, LangPref,
)
from app.api.v1.dependencies import get_current_user

router = APIRouter(prefix="/admin", tags=["Block Admin / Program Manager"])


# ── Helpers ────────────────────────────────────────────────────────────────────

async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role not in (UserRole.block_admin, UserRole.pi, UserRole.super_admin):
        raise ForbiddenException("Admin access required")
    return user


async def get_bpcr_score_for_ben(beneficiary_id: UUID, db: AsyncSession) -> Optional[int]:
    result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.beneficiary_id == beneficiary_id)
        .order_by(desc(BPCRAssessment.assessed_at))
    )
    assessments = result.scalars().all()
    if not assessments:
        return None
    seen = {}
    for a in assessments:
        if a.component not in seen:
            seen[a.component] = a
    return sum(a.score or 0 for a in seen.values())


# ── Admin Login (Email + Password) ─────────────────────────────────────────────

@router.post("/login", summary="Admin login with email and password")
async def admin_login(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Admin/PI/Block Admin login uses email + password (not OTP).
    Returns JWT tokens same format as OTP login.
    """
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise UnauthorizedException("Email and password are required")

    # Find admin credential
    result = await db.execute(
        select(AdminCredential).where(AdminCredential.email == email)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise UnauthorizedException("Invalid email or password")

    if not verify_password(password, cred.password_hash):
        raise UnauthorizedException("Invalid email or password")

    # Get user
    user_result = await db.execute(
        select(User).where(User.id == cred.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedException("Account is inactive")

    if user.role not in (UserRole.block_admin, UserRole.pi, UserRole.super_admin):
        raise ForbiddenException("This account does not have admin access")

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return success_envelope({
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": email,
            "role": user.role.value,
        },
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
    })


# ── A. Overview Panel ──────────────────────────────────────────────────────────

@router.get("/overview", summary="A. Overview Panel — key program metrics")
async def get_overview(
    district: Optional[str] = Query(None),
    block: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns:
    - Total registered users (women, ASHA, ANM)
    - Active users (logged in last 30 days)
    - Drop-off users (registered but never logged in again)
    - Average BPCR score across all beneficiaries
    - Active alerts (pending)
    - OKR summary
    """
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # Base beneficiary query with optional filters
    ben_query = select(Beneficiary)
    if block:
        ben_query = ben_query.where(Beneficiary.block == block)
    if district:
        ben_query = ben_query.where(Beneficiary.district == district)

    ben_result = await db.execute(ben_query)
    beneficiaries = ben_result.scalars().all()
    total_women = len(beneficiaries)

    # Total users by role
    total_asha_res = await db.execute(
        select(func.count(User.id)).where(User.role == UserRole.asha)
    )
    total_asha = total_asha_res.scalar() or 0

    total_anm_res = await db.execute(
        select(func.count(User.id)).where(User.role == UserRole.anm)
    )
    total_anm = total_anm_res.scalar() or 0

    # Active users (last 30 days)
    active_res = await db.execute(
    select(func.count(Beneficiary.id))
    .join(User, User.id == Beneficiary.user_id)
    .where(User.last_login_at >= thirty_days_ago)
    )
    active_women = active_res.scalar() or 0

    # Drop-off — registered but never came back
    dropoff_res = await db.execute(
        select(func.count(User.id))
        .where(User.role == UserRole.pregnant_woman)
        .where(User.last_login_at == None)  # noqa
    )
    dropoff = dropoff_res.scalar() or 0

    # Average BPCR score
    total_score = 0
    bpcr_count = 0
    red_count = 0
    yellow_count = 0
    green_count = 0

    for b in beneficiaries:
        score = await get_bpcr_score_for_ben(b.id, db)
        if score is not None:
            total_score += score
            bpcr_count += 1
            if score >= 8:
                green_count += 1
            elif score >= 5:
                yellow_count += 1
            else:
                red_count += 1

    avg_bpcr = round(total_score / bpcr_count, 1) if bpcr_count > 0 else 0

    # Active alerts
    active_alert_res = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.status == AlertStatus.pending)
    )
    active_alerts = active_alert_res.scalar() or 0

    # Red alerts
    red_alert_res = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.status == AlertStatus.pending)
        .where(Alert.severity == RiskLevel.red)
    )
    red_alerts = red_alert_res.scalar() or 0

    # High risk beneficiaries
    high_risk_res = await db.execute(
        select(func.count(Beneficiary.id))
        .where(Beneficiary.risk_level.in_([RiskLevel.red, RiskLevel.yellow]))
    )
    high_risk = high_risk_res.scalar() or 0

    # ANC overdue count
    anc_overdue = 0
    for b in beneficiaries:
        anc_res = await db.execute(
            select(ANCVisit)
            .where(ANCVisit.beneficiary_id == b.id)
            .order_by(desc(ANCVisit.visit_date))
            .limit(1)
        )
        last_anc = anc_res.scalar_one_or_none()
        if last_anc and last_anc.next_due_date and last_anc.next_due_date < date.today():
            anc_overdue += 1

    return success_envelope({
        "total_registered": {
            "women": total_women,
            "asha": total_asha,
            "anm": total_anm,
            "total": total_women + total_asha + total_anm,
        },
        "active_users": {
            "women_last_30_days": active_women,
            "active_rate_pct": round(active_women / total_women * 100, 1) if total_women > 0 else 0,
        },
        "drop_off": {
            "count": dropoff,
            "rate_pct": round(dropoff / total_women * 100, 1) if total_women > 0 else 0,
        },
        "bpcr": {
            "average_score": avg_bpcr,
            "assessed_count": bpcr_count,
            "not_assessed": total_women - bpcr_count,
            "green_count": green_count,
            "yellow_count": yellow_count,
            "red_count": red_count,
        },
        "alerts": {
            "active_pending": active_alerts,
            "red_alerts": red_alerts,
        },
        "high_risk_pregnancies": high_risk,
        "anc_overdue": anc_overdue,
        "filters_applied": {"district": district, "block": block},
    })


# ── B. BPCR Analytics ──────────────────────────────────────────────────────────

@router.get("/bpcr/distribution", summary="B. BPCR score distribution — Red / Yellow / Green")
async def get_bpcr_distribution(
    block: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ben_query = select(Beneficiary)
    if block:
        ben_query = ben_query.where(Beneficiary.block == block)
    if district:
        ben_query = ben_query.where(Beneficiary.district == district)

    ben_result = await db.execute(ben_query)
    beneficiaries = ben_result.scalars().all()

    distribution = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0,
                    "5": 0, "6": 0, "7": 0, "8": 0, "9": 0, "10": 0}
    red = yellow = green = not_assessed = 0

    for b in beneficiaries:
        score = await get_bpcr_score_for_ben(b.id, db)
        if score is None:
            not_assessed += 1
            continue
        distribution[str(score)] = distribution.get(str(score), 0) + 1
        if score >= 8:
            green += 1
        elif score >= 5:
            yellow += 1
        else:
            red += 1

    total_assessed = red + yellow + green
    return success_envelope({
        "summary": {
            "red": {"count": red, "label": "Poor (0-4)", "pct": round(red / total_assessed * 100, 1) if total_assessed else 0},
            "yellow": {"count": yellow, "label": "Moderate (5-7)", "pct": round(yellow / total_assessed * 100, 1) if total_assessed else 0},
            "green": {"count": green, "label": "Good (8-10)", "pct": round(green / total_assessed * 100, 1) if total_assessed else 0},
        },
        "score_distribution": distribution,
        "total_assessed": total_assessed,
        "not_assessed": not_assessed,
        "chart_type": "bar",
    })


@router.get("/bpcr/trend", summary="B. BPCR trend — score improvement over time")
async def get_bpcr_trend(
    days: int = Query(30, description="Number of days to look back"),
    block: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns weekly average BPCR scores for trend line chart."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.assessed_at >= since)
        .order_by(BPCRAssessment.assessed_at)
    )
    assessments = result.scalars().all()

    # Group by week
    weekly: dict = {}
    for a in assessments:
        assessed = a.assessed_at
        if assessed.tzinfo is None:
            assessed = assessed.replace(tzinfo=timezone.utc)
        week_start = (assessed - timedelta(days=assessed.weekday())).date()
        week_key = week_start.isoformat()
        if week_key not in weekly:
            weekly[week_key] = {"scores": [], "count": 0}
        weekly[week_key]["scores"].append(a.score or 0)
        weekly[week_key]["count"] += 1

    trend = [
        {
            "week": k,
            "avg_score": round(sum(v["scores"]) / len(v["scores"]), 2),
            "assessments_count": v["count"],
        }
        for k, v in sorted(weekly.items())
    ]

    return success_envelope({
        "trend": trend,
        "period_days": days,
        "chart_type": "line",
    })


@router.get("/bpcr/block-comparison", summary="B. Block-wise BPCR comparison")
async def get_bpcr_block_comparison(
    district: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bar chart data — Block A vs Block B BPCR average scores."""
    ben_query = select(Beneficiary)
    if district:
        ben_query = ben_query.where(Beneficiary.district == district)

    ben_result = await db.execute(ben_query)
    beneficiaries = ben_result.scalars().all()

    blocks: dict = {}
    for b in beneficiaries:
        block_name = b.block or "Unknown"
        if block_name not in blocks:
            blocks[block_name] = {"scores": [], "red": 0, "yellow": 0, "green": 0, "total": 0}

        score = await get_bpcr_score_for_ben(b.id, db)
        blocks[block_name]["total"] += 1
        if score is not None:
            blocks[block_name]["scores"].append(score)
            if score >= 8:
                blocks[block_name]["green"] += 1
            elif score >= 5:
                blocks[block_name]["yellow"] += 1
            else:
                blocks[block_name]["red"] += 1

    comparison = [
        {
            "block": k,
            "avg_score": round(sum(v["scores"]) / len(v["scores"]), 2) if v["scores"] else 0,
            "total_beneficiaries": v["total"],
            "assessed": len(v["scores"]),
            "red": v["red"],
            "yellow": v["yellow"],
            "green": v["green"],
        }
        for k, v in sorted(blocks.items())
    ]

    return success_envelope({
        "blocks": comparison,
        "chart_type": "bar",
        "note": "Block A vs Block B comparison for program manager review",
    })


# ── C. Alert Monitoring ────────────────────────────────────────────────────────

@router.get("/alerts", summary="C. Alert Monitoring — all alerts with filters")
async def get_all_alerts(
    status: Optional[str] = Query(None, description="pending|contacted|referred|resolved|closed"),
    severity: Optional[str] = Query(None, description="low|yellow|red"),
    block: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None, description="danger_sign|missed_anc|bpcr_low"),
    days: int = Query(7, description="Last N days"),
    limit: int = Query(50),
    offset: int = Query(0),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(Alert).where(Alert.triggered_at >= since)

    if status:
        try:
            query = query.where(Alert.status == AlertStatus(status))
        except ValueError:
            pass
    if severity:
        try:
            query = query.where(Alert.severity == RiskLevel(severity))
        except ValueError:
            pass
    if alert_type:
        try:
            query = query.where(Alert.alert_type == AlertType(alert_type))
        except ValueError:
            pass

    query = query.order_by(desc(Alert.triggered_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    alerts = result.scalars().all()

    # Total count
    count_query = select(func.count(Alert.id)).where(Alert.triggered_at >= since)
    if status:
        try:
            count_query = count_query.where(Alert.status == AlertStatus(status))
        except ValueError:
            pass
    count_res = await db.execute(count_query)
    total = count_res.scalar() or 0

    now = datetime.now(timezone.utc)
    items = []
    for alert in alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if not ben:
            continue

        # Apply block/district filter on beneficiary
        if block and ben.block != block:
            continue
        if district and ben.district != district:
            continue

        user_res = await db.execute(select(User).where(User.id == ben.user_id))
        ben_user = user_res.scalar_one_or_none()

        triggered = alert.triggered_at
        if triggered.tzinfo is None:
            triggered = triggered.replace(tzinfo=timezone.utc)
        mins = int((now - triggered).total_seconds() / 60)

        items.append({
            "id": str(alert.id),
            "beneficiary_id": str(ben.id),
            "name": ben.name,
            "village": ben.village,
            "block": ben.block,
            "district": ben.district,
            "mobile": ben_user.mobile if ben_user else None,
            "symptoms": alert.symptoms,
            "alert_type": alert.alert_type.value,
            "risk_level": alert.severity.value,
            "status": alert.status.value,
            "triggered_at": alert.triggered_at.isoformat(),
            "time_ago_minutes": mins,
            "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
            "response_minutes": int((alert.responded_at.replace(tzinfo=timezone.utc) - triggered).total_seconds() / 60)
                if alert.responded_at else None,
        })

    return success_envelope({
        "alerts": items,
        "total": total,
        "returned": len(items),
        "filters": {"status": status, "severity": severity, "block": block, "days": days},
    })


@router.get("/alerts/{alert_id}", summary="C. Alert detail")
async def get_alert_detail(
    alert_id: UUID,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise NotFoundException("Alert")

    ben_res = await db.execute(
        select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
    )
    ben = ben_res.scalar_one_or_none()
    user_res = await db.execute(select(User).where(User.id == ben.user_id))
    ben_user = user_res.scalar_one_or_none()

    # Assigned ASHA
    asha_name = None
    if alert.assigned_to:
        worker_res = await db.execute(
            select(FieldWorker).where(FieldWorker.id == alert.assigned_to)
        )
        worker = worker_res.scalar_one_or_none()
        if worker:
            asha_user_res = await db.execute(
                select(User).where(User.id == worker.user_id)
            )
            asha_user = asha_user_res.scalar_one_or_none()
            asha_name = asha_user.name if asha_user else None

    triggered = alert.triggered_at
    now = datetime.now(timezone.utc)
    if triggered.tzinfo is None:
        triggered = triggered.replace(tzinfo=timezone.utc)
    mins = int((now - triggered).total_seconds() / 60)

    return success_envelope({
        "id": str(alert.id),
        "beneficiary": {
            "id": str(ben.id),
            "name": ben.name,
            "age": ben.age,
            "mobile": ben_user.mobile if ben_user else None,
            "village": ben.village,
            "block": ben.block,
            "district": ben.district,
            "address": ben.address,
        },
        "alert_type": alert.alert_type.value,
        "severity": alert.severity.value,
        "symptoms": alert.symptoms,
        "notes": alert.notes,
        "status": alert.status.value,
        "assigned_asha": asha_name,
        "triggered_at": alert.triggered_at.isoformat(),
        "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
        "closed_at": alert.closed_at.isoformat() if alert.closed_at else None,
        "time_elapsed_minutes": mins,
        "is_sla_breached": mins > settings.ALERT_SLA_HOURS * 60 and alert.status == AlertStatus.pending,
    })


# ── D. Response Time Tracking ──────────────────────────────────────────────────

@router.get("/response-time", summary="D. Response time tracking — SLA metrics")
async def get_response_time(
    days: int = Query(30),
    block: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(Alert).where(Alert.triggered_at >= since)
    )
    alerts = result.scalars().all()

    total = len(alerts)
    responded = [a for a in alerts if a.responded_at]
    within_sla = []
    delayed = []
    response_times = []

    sla_minutes = settings.ALERT_SLA_HOURS * 60

    for a in responded:
        triggered = a.triggered_at
        responded_at = a.responded_at
        if triggered.tzinfo is None:
            triggered = triggered.replace(tzinfo=timezone.utc)
        if responded_at.tzinfo is None:
            responded_at = responded_at.replace(tzinfo=timezone.utc)
        mins = int((responded_at - triggered).total_seconds() / 60)
        response_times.append(mins)
        if mins <= sla_minutes:
            within_sla.append(a)
        else:
            delayed.append(a)

    avg_response = round(sum(response_times) / len(response_times), 1) if response_times else 0
    pct_within_sla = round(len(within_sla) / len(responded) * 100, 1) if responded else 0

    # Block-wise breakdown
    block_breakdown: dict = {}
    for a in alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == a.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if not ben:
            continue
        b_block = ben.block or "Unknown"
        if b_block not in block_breakdown:
            block_breakdown[b_block] = {"total": 0, "responded": 0, "within_sla": 0, "times": []}
        block_breakdown[b_block]["total"] += 1
        if a.responded_at:
            triggered = a.triggered_at
            resp = a.responded_at
            if triggered.tzinfo is None:
                triggered = triggered.replace(tzinfo=timezone.utc)
            if resp.tzinfo is None:
                resp = resp.replace(tzinfo=timezone.utc)
            mins = int((resp - triggered).total_seconds() / 60)
            block_breakdown[b_block]["responded"] += 1
            block_breakdown[b_block]["times"].append(mins)
            if mins <= sla_minutes:
                block_breakdown[b_block]["within_sla"] += 1

    block_stats = [
        {
            "block": k,
            "total_alerts": v["total"],
            "responded": v["responded"],
            "avg_response_minutes": round(sum(v["times"]) / len(v["times"]), 1) if v["times"] else 0,
            "within_sla_pct": round(v["within_sla"] / v["responded"] * 100, 1) if v["responded"] else 0,
        }
        for k, v in sorted(block_breakdown.items())
    ]

    return success_envelope({
        "summary": {
            "total_alerts": total,
            "total_responded": len(responded),
            "avg_response_minutes": avg_response,
            "pct_within_sla": pct_within_sla,
            "sla_threshold_minutes": sla_minutes,
            "delayed_cases": len(delayed),
            "pending_unresponded": total - len(responded),
        },
        "block_breakdown": block_stats,
        "period_days": days,
        "chart_type": "mixed",
    })


# ── E. User Engagement ─────────────────────────────────────────────────────────

@router.get("/engagement", summary="E. User engagement — DAU, reminders, module usage")
async def get_engagement(
    days: int = Query(30),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Daily active users per day (last N days)
    dau_data = []
    for i in range(min(days, 30)):
        day = date.today() - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        dau_res = await db.execute(
            select(func.count(User.id))
            .where(User.last_login_at >= day_start)
            .where(User.last_login_at < day_end)
            .where(User.role == UserRole.pregnant_woman)
        )
        dau_data.append({"date": day.isoformat(), "active_users": dau_res.scalar() or 0})
    dau_data.reverse()

    # Reminders sent
    reminders_sent_res = await db.execute(
        select(func.count(Reminder.id))
        .where(Reminder.status == "sent")
    )
    reminders_sent = reminders_sent_res.scalar() or 0

    reminders_pending_res = await db.execute(
        select(func.count(Reminder.id))
        .where(Reminder.status == "pending")
    )
    reminders_pending = reminders_pending_res.scalar() or 0

    # Module usage — count by alert type as proxy for module used
    bpcr_usage_res = await db.execute(
        select(func.count(BPCRAssessment.id))
        .where(BPCRAssessment.assessed_at >= since)
    )
    bpcr_usage = bpcr_usage_res.scalar() or 0

    anc_usage_res = await db.execute(
        select(func.count(ANCVisit.id))
        .where(ANCVisit.created_at >= since)
    )
    anc_usage = anc_usage_res.scalar() or 0

    danger_usage_res = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.alert_type == AlertType.danger_sign)
        .where(Alert.triggered_at >= since)
    )
    danger_usage = danger_usage_res.scalar() or 0

    most_used = max(
        [("ANC", anc_usage), ("BPCR", bpcr_usage), ("Danger Sign", danger_usage)],
        key=lambda x: x[1]
    )[0]

    return success_envelope({
        "daily_active_users": dau_data,
        "reminders": {
            "sent": reminders_sent,
            "pending": reminders_pending,
            "types": ["ANC visit", "Medicine", "Follow-up"],
        },
        "module_usage": {
            "anc": anc_usage,
            "bpcr": bpcr_usage,
            "danger_sign": danger_usage,
            "most_used": most_used,
        },
        "period_days": days,
        "chart_type": "line",
    })


# ── F. GIS / Map ───────────────────────────────────────────────────────────────

@router.get("/gis/high-risk", summary="F. GIS — high risk beneficiary coordinates for map")
async def get_gis_high_risk(
    district: Optional[str] = Query(None),
    block: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Beneficiary).where(
        Beneficiary.risk_level.in_([RiskLevel.red, RiskLevel.yellow])
    ).where(Beneficiary.latitude != None)  # noqa

    if district:
        query = query.where(Beneficiary.district == district)
    if block:
        query = query.where(Beneficiary.block == block)

    result = await db.execute(query)
    beneficiaries = result.scalars().all()

    points = []
    for b in beneficiaries:
        points.append({
            "id": str(b.id),
            "name": b.name,
            "latitude": b.latitude,
            "longitude": b.longitude,
            "risk_level": b.risk_level.value if b.risk_level else "low",
            "village": b.village,
            "block": b.block,
            "gestational_week": min(((date.today() - b.lmp).days) // 7, 42) if b.lmp else None,
        })

    return success_envelope({
        "points": points,
        "total": len(points),
        "map_type": "risk_heatmap",
    })


@router.get("/gis/alerts", summary="F. GIS — alert location coordinates for map")
async def get_gis_alerts(
    status: Optional[str] = Query("pending"),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Alert)
    if status:
        try:
            query = query.where(Alert.status == AlertStatus(status))
        except ValueError:
            pass

    result = await db.execute(query.order_by(desc(Alert.triggered_at)).limit(200))
    alerts = result.scalars().all()

    points = []
    for alert in alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if not ben or not ben.latitude:
            continue
        points.append({
            "alert_id": str(alert.id),
            "beneficiary_id": str(ben.id),
            "name": ben.name,
            "latitude": ben.latitude,
            "longitude": ben.longitude,
            "severity": alert.severity.value,
            "alert_type": alert.alert_type.value,
            "status": alert.status.value,
            "village": ben.village,
            "triggered_at": alert.triggered_at.isoformat(),
        })

    return success_envelope({"points": points, "total": len(points)})


# ── G. Data Export ─────────────────────────────────────────────────────────────

def make_csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),  # utf-8-sig for Excel Hindi support
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/bpcr", summary="G. Export BPCR data as CSV")
async def export_bpcr(
    block: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ben_query = select(Beneficiary)
    if block:
        ben_query = ben_query.where(Beneficiary.block == block)
    if district:
        ben_query = ben_query.where(Beneficiary.district == district)

    ben_result = await db.execute(ben_query)
    beneficiaries = ben_result.scalars().all()

    rows = []
    for b in beneficiaries:
        score = await get_bpcr_score_for_ben(b.id, db)
        risk = "Green" if score and score >= 8 else ("Yellow" if score and score >= 5 else "Red") if score is not None else "Not Assessed"
        rows.append([
            str(b.id), b.name, b.age or "", b.village or "",
            b.block or "", b.district or "",
            b.lmp.isoformat() if b.lmp else "",
            score if score is not None else "Not Assessed",
            risk,
            b.risk_level.value if b.risk_level else "",
        ])

    headers = ["ID", "Name", "Age", "Village", "Block", "District", "LMP", "BPCR Score", "BPCR Risk", "Overall Risk"]
    return make_csv_response(rows, headers, f"bpcr_export_{date.today()}.csv")


@router.get("/export/users", summary="G. Export user data as CSV")
async def export_users(
    role: Optional[str] = Query(None, description="pregnant_woman|asha|anm"),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)
    if role:
        try:
            query = query.where(User.role == UserRole(role))
        except ValueError:
            pass

    result = await db.execute(query.order_by(User.created_at))
    users = result.scalars().all()

    rows = []
    for u in users:
        rows.append([
            str(u.id), u.mobile, u.name or "", u.role.value,
            u.preferred_language.value if u.preferred_language else "hi",
            "Yes" if u.is_active else "No",
            u.last_login_at.isoformat() if u.last_login_at else "Never",
            u.created_at.isoformat() if u.created_at else "",
        ])

    headers = ["ID", "Mobile", "Name", "Role", "Language", "Active", "Last Login", "Registered At"]
    return make_csv_response(rows, headers, f"users_export_{date.today()}.csv")


@router.get("/export/alerts", summary="G. Export alert logs as CSV")
async def export_alerts(
    days: int = Query(30),
    block: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Alert)
        .where(Alert.triggered_at >= since)
        .order_by(desc(Alert.triggered_at))
    )
    alerts = result.scalars().all()

    rows = []
    for a in alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == a.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if not ben:
            continue
        if block and ben.block != block:
            continue

        response_mins = ""
        if a.responded_at:
            t = a.triggered_at
            r = a.responded_at
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if r.tzinfo is None:
                r = r.replace(tzinfo=timezone.utc)
            response_mins = int((r - t).total_seconds() / 60)

        rows.append([
            str(a.id), ben.name, ben.village or "", ben.block or "", ben.district or "",
            a.alert_type.value, a.severity.value, a.status.value,
            a.triggered_at.isoformat(),
            a.responded_at.isoformat() if a.responded_at else "Not responded",
            response_mins,
        ])

    headers = [
        "Alert ID", "Beneficiary Name", "Village", "Block", "District",
        "Alert Type", "Severity", "Status",
        "Triggered At", "Responded At", "Response Time (mins)"
    ]
    return make_csv_response(rows, headers, f"alerts_export_{date.today()}.csv")


# ── H. Settings ────────────────────────────────────────────────────────────────

@router.get("/users", summary="H. List all admin, ASHA, ANM users")
async def list_admin_users(
    role: Optional[str] = Query(None),
    block: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(User).where(
        User.role.in_([UserRole.asha, UserRole.anm, UserRole.block_admin, UserRole.pi, UserRole.super_admin])
    )
    if role:
        try:
            query = query.where(User.role == UserRole(role))
        except ValueError:
            pass

    result = await db.execute(query.order_by(User.created_at))
    users = result.scalars().all()

    items = []
    for u in users:
        worker_res = await db.execute(
            select(FieldWorker).where(FieldWorker.user_id == u.id)
        )
        worker = worker_res.scalar_one_or_none()

        if block and worker and worker.block != block:
            continue

        items.append({
            "id": str(u.id),
            "name": u.name,
            "mobile": u.mobile,
            "role": u.role.value,
            "is_active": u.is_active,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "block": worker.block if worker else None,
            "district": worker.district if worker else None,
            "unique_id": worker.unique_id if worker else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return success_envelope({"users": items, "total": len(items)})


@router.post("/users", summary="H. Create new block admin or BMO user")
async def create_admin_user(
    body: dict,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Only super_admin or PI can create new admin users.
    Creates user + admin_credentials (email+password) in one step.
    """
    if user.role not in (UserRole.super_admin, UserRole.pi):
        raise ForbiddenException("Only PI or Super Admin can create admin users")

    name = body.get("name", "").strip()
    mobile = body.get("mobile", "").strip()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "").strip()
    role = body.get("role", "block_admin")
    block = body.get("block", "")
    district = body.get("district", "")

    if not all([name, mobile, email, password, block, district]):
        raise ConflictException("name, mobile, email, password, block, district are all required")

    # Check mobile not taken
    existing = await db.execute(select(User).where(User.mobile == mobile))
    if existing.scalar_one_or_none():
        raise ConflictException(f"Mobile {mobile} already registered")

    # Check email not taken
    email_check = await db.execute(
        select(AdminCredential).where(AdminCredential.email == email)
    )
    if email_check.scalar_one_or_none():
        raise ConflictException(f"Email {email} already registered")

    try:
        user_role = UserRole(role)
    except ValueError:
        raise ConflictException(f"Invalid role: {role}")

    new_user = User(
        mobile=mobile,
        name=name,
        role=user_role,
        is_active=True,
    )
    db.add(new_user)
    await db.flush()

    cred = AdminCredential(
        user_id=new_user.id,
        email=email,
        password_hash=hash_password(password),
    )
    db.add(cred)

    # If ASHA/ANM, also create field worker record
    if role in ("asha", "anm"):
        worker = FieldWorker(
            user_id=new_user.id,
            worker_role=role,
            block=block,
            district=district,
            unique_id=body.get("unique_id", f"{role.upper()}-{mobile}"),
        )
        db.add(worker)

    await db.commit()
    await db.refresh(new_user)

    return success_envelope({
        "message": "User created successfully",
        "user_id": str(new_user.id),
        "name": name,
        "role": role,
        "email": email,
    })


@router.patch("/users/{user_id}", summary="H. Update user role or active status")
async def update_admin_user(
    user_id: UUID,
    body: dict,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.super_admin, UserRole.pi):
        raise ForbiddenException("Only PI or Super Admin can update users")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundException("User")

    if "is_active" in body:
        target.is_active = bool(body["is_active"])
    if "name" in body:
        target.name = body["name"]
    if "role" in body:
        try:
            target.role = UserRole(body["role"])
        except ValueError:
            pass

    await db.commit()
    return success_envelope({"message": "User updated", "user_id": str(target.id)})


@router.delete("/users/{user_id}", summary="H. Deactivate a user")
async def deactivate_user(
    user_id: UUID,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.super_admin, UserRole.pi):
        raise ForbiddenException("Only PI or Super Admin can deactivate users")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundException("User")

    target.is_active = False
    await db.commit()
    return success_envelope({"message": "User deactivated", "user_id": str(target.id)})


@router.get("/settings", summary="H. Get system settings")
async def get_settings_api(
    user: User = Depends(get_current_admin),
):
    return success_envelope({
        "alert_sla_hours": settings.ALERT_SLA_HOURS,
        "bpcr_alert_threshold": settings.BPCR_ALERT_THRESHOLD,
        "anc_overdue_days": settings.ANC_OVERDUE_DAYS,
        "otp_expire_minutes": settings.OTP_EXPIRE_MINUTES,
        "supported_languages": ["hi", "en"],
        "default_language": "hi",
        "app_version": settings.VERSION,
    })


@router.patch("/settings", summary="H. Update alert threshold and system settings")
async def update_settings_api(
    body: dict,
    user: User = Depends(get_current_admin),
):
    """
    Note: Settings are env-based. This endpoint validates and returns
    what would be applied. In production, update .env and restart.
    For runtime changes, these would be stored in a settings DB table.
    """
    if user.role != UserRole.super_admin:
        raise ForbiddenException("Only Super Admin can change system settings")

    updatable = ["alert_sla_hours", "bpcr_alert_threshold", "anc_overdue_days"]
    applied = {k: v for k, v in body.items() if k in updatable}

    return success_envelope({
        "message": "Settings noted. Apply to .env and restart server for persistence.",
        "applied": applied,
        "note": "Runtime-only in current version. DB-backed settings coming in v2.",
    })

# ── I . Facility Management (Admin) ────────────────────────────────────────────────

@router.get("/facilities", summary="List all health facilities",
            operation_id="admin_list_facilities")
async def admin_list_facilities(
    facility_type: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    sub_district: Optional[str] = Query(None),
    is_functional: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import HealthFacility

    query = select(HealthFacility)
    if facility_type:
        query = query.where(HealthFacility.facility_type == facility_type.upper())
    if district:
        query = query.where(HealthFacility.district.ilike(f"%{district}%"))
    if sub_district:
        query = query.where(HealthFacility.sub_district.ilike(f"%{sub_district}%"))
    if is_functional is not None:
        query = query.where(HealthFacility.is_functional == is_functional)
    if search:
        query = query.where(HealthFacility.name.ilike(f"%{search}%"))

    result = await db.execute(query.order_by(HealthFacility.facility_type, HealthFacility.name))
    facilities = result.scalars().all()

    # Summary counts
    type_counts: dict = {}
    for f in facilities:
        type_counts[f.facility_type] = type_counts.get(f.facility_type, 0) + 1

    return success_envelope({
        "facilities": [{
            "id": str(f.id),
            "name": f.name,
            "facility_type": f.facility_type,
            "district": f.district,
            "sub_district": f.sub_district,
            "block": f.block,
            "rural_urban": f.rural_urban,
            "is_fru": f.is_fru,
            "is_24x7": f.is_24x7,
            "has_labour_room": f.has_labour_room,
            "has_blood_bank": f.has_blood_bank,
            "is_functional": f.is_functional,
            "phone": f.phone,
            "anc_registrations": f.anc_registrations,
            "category": f.category,
            "latitude": f.latitude,
            "longitude": f.longitude,
            "last_updated": f.last_updated.isoformat() if f.last_updated else None,
        } for f in facilities],
        "total": len(facilities),
        "summary": type_counts,
    })


@router.post("/facilities", summary="Add a new health facility",
             operation_id="admin_create_facility")
async def admin_create_facility(
    body: dict,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import HealthFacility

    name = body.get("name", "").strip()
    facility_type = body.get("facility_type", "").upper()
    district = body.get("district", "").strip()

    if not all([name, facility_type, district]):
        raise ConflictException("name, facility_type, district are required")

    valid_types = ["MC", "DH", "SDH", "CHC", "PHC", "SHC"]
    if facility_type not in valid_types:
        raise ConflictException(f"facility_type must be one of: {valid_types}")

    facility = HealthFacility(
        name=name,
        facility_type=facility_type,
        district=district,
        sub_district=body.get("sub_district"),
        block=body.get("block"),
        rural_urban=body.get("rural_urban", "Rural"),
        is_fru=body.get("is_fru", False),
        is_24x7=body.get("is_24x7", False),
        has_labour_room=body.get("has_labour_room", False),
        has_blood_bank=body.get("has_blood_bank", False),
        is_functional=body.get("is_functional", True),
        phone=body.get("phone"),
        latitude=body.get("latitude"),
        longitude=body.get("longitude"),
        remarks=body.get("remarks"),
    )
    db.add(facility)
    await db.commit()
    await db.refresh(facility)

    return success_envelope({
        "message": "Facility added successfully",
        "facility_id": str(facility.id),
        "name": facility.name,
    })


@router.patch("/facilities/{facility_id}", summary="Update facility details",
              operation_id="admin_update_facility")
async def admin_update_facility(
    facility_id: UUID,
    body: dict,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import HealthFacility

    result = await db.execute(
        select(HealthFacility).where(HealthFacility.id == facility_id)
    )
    facility = result.scalar_one_or_none()
    if not facility:
        raise NotFoundException("Facility")

    updatable = [
        "name", "phone", "is_24x7", "has_labour_room", "has_blood_bank",
        "is_functional", "is_fru", "latitude", "longitude",
        "rural_urban", "category", "anc_registrations", "remarks",
    ]
    for field in updatable:
        if field in body:
            setattr(facility, field, body[field])

    await db.commit()
    return success_envelope({
        "message": "Facility updated",
        "facility_id": str(facility.id),
    })


@router.delete("/facilities/{facility_id}", summary="Deactivate a facility",
               operation_id="admin_deactivate_facility")
async def admin_deactivate_facility(
    facility_id: UUID,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import HealthFacility

    if user.role not in (UserRole.super_admin, UserRole.pi):
        raise ForbiddenException("Only PI or Super Admin can deactivate facilities")

    result = await db.execute(
        select(HealthFacility).where(HealthFacility.id == facility_id)
    )
    facility = result.scalar_one_or_none()
    if not facility:
        raise NotFoundException("Facility")

    facility.is_functional = False
    await db.commit()
    return success_envelope({
        "message": "Facility deactivated",
        "facility_id": str(facility.id),
    })