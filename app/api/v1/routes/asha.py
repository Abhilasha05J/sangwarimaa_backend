"""
ASHA / ANM Routes
=================
POST   /api/v1/mitanin/register                    → Register as mitanin/ANM worker
GET    /api/v1/mitanin/profile                     → Get own profile + stats
PATCH  /api/v1/mitanin/profile                     → Update profile

GET    /api/v1/mitanin/dashboard                   → Home dashboard with counts + alerts

GET    /api/v1/mitanin/beneficiaries               → List all assigned beneficiaries
GET    /api/v1/mitanin/beneficiaries/{id}          → Beneficiary full detail
PATCH  /api/v1/mitanin/beneficiaries/{id}/risk     → Update risk level

GET    /api/v1/mitanin/alerts                      → List all alerts (pending first)
GET    /api/v1/mitanin/alerts/{id}                 → Alert detail
PATCH  /api/v1/mitanin/alerts/{id}/status          → Update alert status

POST   /api/v1/mitanin/anc-visits                  → Record a new ANC visit
GET    /api/v1/mitanin/anc-visits/{beneficiary_id} → ANC history for a beneficiary

POST   /api/v1/mitanin/bpcr                        → Submit BPCR for a beneficiary

GET    /api/v1/mitanin/overdue-anc                 → List women with overdue ANC
GET    /api/v1/mitanin/high-risk  

GET  /api/v1/mitanin/facilities                    → Facility location list + distance
GET  /api/v1/mitanin/accountability                → Safety/accountability screen
POST /api/v1/mitanin/visit-log                     → Log a visit/activity
GET  /api/v1/mitanin/visit-log/{beneficiary_id}    → Visit log history
                 → List high-risk women (red/yellow)
"""

from datetime import date, datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload
import math

from app.core.database import get_db
from app.core.exceptions import (
    NotFoundException, ConflictException,
    ValidationException, ForbiddenException,
    success_envelope,
)
from app.models.models import (
    User, FieldWorker, Beneficiary, Alert, ANCVisit,
    BPCRAssessment, AlertStatus, AlertType, RiskLevel,
    UserRole,
)
from app.schemas.asha import (
    ASHARegisterRequest, ASHAProfileUpdate,
    ANCVisitCreate, ANCVisitOut,
    AlertStatusUpdate, ASHABPCRRequest,
)
from app.api.v1.dependencies import get_current_user, get_current_field_worker

router = APIRouter(prefix="/mitanin", tags=["Field Worker (Mitanin)"])

BPCR_COMPONENTS = [
    "birth_place", "skilled_birth_attendant", "transport",
    "emergency_funds", "blood_donor", "decision_maker",
    "support_person", "danger_sign_knowledge",
    "newborn_care", "postpartum_care",
]


# ── Helpers ────────────────────────────────────────────────────────────────────
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two coordinates."""
    import math
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def compute_pregnancy(lmp: date) -> dict:
    today = date.today()
    days = (today - lmp).days
    week = min(days // 7, 42)
    trimester = 1 if week < 14 else (2 if week < 28 else 3)
    edd = lmp + timedelta(days=280)
    return {
        "gestational_week": week,
        "trimester": trimester,
        "edd": edd,
        "days_until_edd": max(0, (edd - today).days),
    }


def get_high_risk_flags(visit: ANCVisit) -> list[str]:
    flags = []
    if visit.bp_systolic and visit.bp_systolic > 140:
        flags.append("high_bp")
    if visit.bp_diastolic and visit.bp_diastolic > 90:
        flags.append("high_diastolic_bp")
    if visit.hemoglobin:
        try:
            hb = float(visit.hemoglobin)
            if hb < 7.0:
                flags.append("severe_anemia")
            elif hb < 11.0:
                flags.append("mild_anemia")
        except ValueError:
            pass
    return flags


def compute_bpcr_risk(score: int) -> str:
    if score >= 8:
        return "Green"
    elif score >= 5:
        return "Yellow"
    return "Red"


async def get_worker_or_404(user: User, db: AsyncSession) -> FieldWorker:
    result = await db.execute(
        select(FieldWorker).where(FieldWorker.user_id == user.id)
    )
    worker = result.scalar_one_or_none()
    if not worker:
        raise NotFoundException("Field worker profile")
    return worker


async def get_bpcr_score(beneficiary_id: UUID, db: AsyncSession) -> Optional[int]:
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


async def build_beneficiary_list_item(b: Beneficiary, db: AsyncSession) -> dict:
    pregnancy = compute_pregnancy(b.lmp)

    # Latest ANC visit
    anc_result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .order_by(desc(ANCVisit.visit_date))
        .limit(1)
    )
    last_anc = anc_result.scalar_one_or_none()

    # Pending alerts count
    alert_result = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.beneficiary_id == b.id)
        .where(Alert.status == AlertStatus.pending)
    )
    pending_alerts = alert_result.scalar() or 0

    # BPCR score
    bpcr_score = await get_bpcr_score(b.id, db)

    # Get user mobile
    user_result = await db.execute(select(User).where(User.id == b.user_id))
    ben_user = user_result.scalar_one_or_none()

    next_due = last_anc.next_due_date if last_anc else None
    is_overdue = next_due is not None and next_due < date.today()

    return {
        "id": str(b.id),
        "name": b.name,
        "age": b.age,
        "village": b.village,
        "lmp": b.lmp.isoformat(),
        "gestational_week": pregnancy["gestational_week"],
        "trimester": pregnancy["trimester"],
        "risk_level": b.risk_level.value if b.risk_level else "low",
        "last_anc_date": last_anc.visit_date.isoformat() if last_anc else None,
        "next_anc_due": next_due.isoformat() if next_due else None,
        "is_anc_overdue": is_overdue,
        "bpcr_score": bpcr_score,
        "mobile": ben_user.mobile if ben_user else None,
        "pending_alerts": pending_alerts,
    }


# ── Registration ───────────────────────────────────────────────────────────────

@router.post("/register", summary="Register as ASHA or ANM worker")
async def register_asha(
    payload: ASHARegisterRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called after OTP verification when is_new_user=True for ASHA/ANM role.
    Admin must have set the user role to 'asha' or 'anm' beforehand,
    OR pass worker_role here and it will be set automatically.
    """
    # Check not already registered
    existing = await db.execute(
        select(FieldWorker).where(FieldWorker.user_id == user.id)
    )
    if existing.scalar_one_or_none():
        raise ConflictException("Worker profile already registered.")

    # Check unique_id not taken
    uid_check = await db.execute(
        select(FieldWorker).where(FieldWorker.unique_id == payload.unique_id)
    )
    if uid_check.scalar_one_or_none():
        raise ConflictException(f"Worker ID '{payload.unique_id}' is already registered.")

    # Update user role and name
    user.name = payload.name
    user.role = UserRole.asha if payload.worker_role == "asha" else UserRole.anm
    user.preferred_language = payload.preferred_language  # type: ignore

    worker = FieldWorker(
        user_id=user.id,
        worker_role=payload.worker_role,
        unique_id=payload.unique_id,
        subcentre=payload.subcentre,
        village=payload.village,
        block=payload.block,
        district=payload.district,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.add(worker)
    await db.commit()
    await db.refresh(worker)

    return success_envelope({
        "message": "Registration successful",
        "worker_id": str(worker.id),
        "worker_role": payload.worker_role,
        "block": payload.block,
        "district": payload.district,
    })


# ── Profile ────────────────────────────────────────────────────────────────────

@router.get("/profile", summary="Get ASHA/ANM profile with stats", operation_id="get_asha_profile")
async def get_asha_profile(
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    # Count assigned beneficiaries
    ben_count = await db.execute(
        select(func.count(Beneficiary.id))
        .where(Beneficiary.asha_id == worker.id)
    )
    total_bens = ben_count.scalar() or 0

    # Count pending alerts
    alert_count = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.assigned_to == worker.id)
        .where(Alert.status == AlertStatus.pending)
    )
    pending_alerts = alert_count.scalar() or 0

    return success_envelope({
        "id": str(worker.id),
        "name": user.name,
        "mobile": user.mobile,
        "worker_role": worker.worker_role,
        "unique_id": worker.unique_id,
        "subcentre": worker.subcentre,
        "village": worker.village,
        "block": worker.block,
        "district": worker.district,
        "preferred_language": user.preferred_language.value if user.preferred_language else "hi",
        "total_beneficiaries": total_bens,
        "pending_alerts": pending_alerts,
    })


@router.patch("/profile", summary="Update ASHA/ANM profile")
async def update_asha_profile(
    payload: ASHAProfileUpdate,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)
    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data:
        user.name = update_data.pop("name")
    if "preferred_language" in update_data:
        user.preferred_language = update_data["preferred_language"]  # type: ignore
    if "fcm_token" in update_data:
        user.fcm_token = update_data.pop("fcm_token")

    for field, value in update_data.items():
        if hasattr(worker, field):
            setattr(worker, field, value)

    await db.commit()
    return success_envelope({"message": "Profile updated successfully"})


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", summary="ASHA home dashboard with key stats")
async def get_dashboard(
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    # Total beneficiaries
    ben_result = await db.execute(
        select(Beneficiary).where(Beneficiary.asha_id == worker.id)
    )
    beneficiaries = ben_result.scalars().all()
    total_bens = len(beneficiaries)

    # Pending alerts
    alert_result = await db.execute(
        select(Alert)
        .where(Alert.assigned_to == worker.id)
        .where(Alert.status == AlertStatus.pending)
        .order_by(desc(Alert.triggered_at))
        .limit(5)
    )
    pending_alerts = alert_result.scalars().all()

    # Red alerts count
    red_result = await db.execute(
        select(func.count(Alert.id))
        .where(Alert.assigned_to == worker.id)
        .where(Alert.status == AlertStatus.pending)
        .where(Alert.severity == RiskLevel.red)
    )
    red_count = red_result.scalar() or 0

    # ANC overdue count
    overdue_count = 0
    upcoming_anc = []
    for b in beneficiaries:
        anc_res = await db.execute(
            select(ANCVisit)
            .where(ANCVisit.beneficiary_id == b.id)
            .order_by(desc(ANCVisit.visit_date))
            .limit(1)
        )
        last_anc = anc_res.scalar_one_or_none()
        if last_anc and last_anc.next_due_date:
            if last_anc.next_due_date < date.today():
                overdue_count += 1
            elif last_anc.next_due_date <= date.today() + timedelta(days=7):
                upcoming_anc.append({
                    "beneficiary_id": str(b.id),
                    "beneficiary_name": b.name,
                    "village": b.village,
                    "due_date": last_anc.next_due_date.isoformat(),
                })

    # Low BPCR count (score < 5)
    low_bpcr = 0
    for b in beneficiaries:
        score = await get_bpcr_score(b.id, db)
        if score is not None and score < 5:
            low_bpcr += 1

    # Build recent alerts
    recent_alerts = []
    for alert in pending_alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if ben:
            user_res = await db.execute(select(User).where(User.id == ben.user_id))
            ben_user = user_res.scalar_one_or_none()
            triggered = alert.triggered_at
            now = datetime.now(timezone.utc)
            if triggered.tzinfo is None:
                triggered = triggered.replace(tzinfo=timezone.utc)
            mins = int((now - triggered).total_seconds() / 60)
            sla_hours = 2
            is_overdue = mins > sla_hours * 60

            recent_alerts.append({
                "id": str(alert.id),
                "beneficiary_id": str(ben.id),
                "beneficiary_name": ben.name,
                "beneficiary_village": ben.village,
                "beneficiary_mobile": ben_user.mobile if ben_user else None,
                "alert_type": alert.alert_type.value,
                "severity": alert.severity.value,
                "symptoms": alert.symptoms,
                "status": alert.status.value,
                "triggered_at": alert.triggered_at.isoformat(),
                "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
                "response_minutes": mins,
                "is_overdue": is_overdue,
            })

    return success_envelope({
        "worker_name": user.name,
        "worker_role": worker.worker_role,
        "block": worker.block,
        "district": worker.district,
        "total_beneficiaries": total_bens,
        "pending_alerts": len(pending_alerts),
        "red_alerts": red_count,
        "anc_overdue": overdue_count,
        "low_bpcr": low_bpcr,
        "recent_alerts": recent_alerts,
        "upcoming_anc": upcoming_anc[:5],
    })


# ── Beneficiaries ──────────────────────────────────────────────────────────────

@router.get("/beneficiaries", summary="List all assigned beneficiaries")
async def list_beneficiaries(
    risk_level: Optional[str] = Query(None, description="Filter: low | yellow | red"),
    search: Optional[str] = Query(None, description="Search by name or village"),
    overdue_only: bool = Query(False, description="Show only ANC overdue women"),
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    query = select(Beneficiary).where(Beneficiary.asha_id == worker.id)

    if risk_level:
        risk_map = {"low": RiskLevel.low, "yellow": RiskLevel.yellow, "red": RiskLevel.red}
        if risk_level in risk_map:
            query = query.where(Beneficiary.risk_level == risk_map[risk_level])

    if search:
        query = query.where(
            Beneficiary.name.ilike(f"%{search}%") |
            Beneficiary.village.ilike(f"%{search}%")
        )

    result = await db.execute(query.order_by(Beneficiary.name))
    beneficiaries = result.scalars().all()

    items = []
    for b in beneficiaries:
        item = await build_beneficiary_list_item(b, db)
        if overdue_only and not item["is_anc_overdue"]:
            continue
        items.append(item)

    return success_envelope({
        "beneficiaries": items,
        "total": len(items),
        "filters": {"risk_level": risk_level, "search": search, "overdue_only": overdue_only},
    })


@router.get("/beneficiaries/{beneficiary_id}", summary="Full beneficiary detail")
async def get_beneficiary_detail(
    beneficiary_id: UUID,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    result = await db.execute(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary")

    # Verify this beneficiary belongs to this worker
    if b.asha_id != worker.id and b.anm_id != worker.id:
        raise ForbiddenException("This beneficiary is not assigned to you")

    pregnancy = compute_pregnancy(b.lmp)

    # ANC visits
    anc_result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .order_by(desc(ANCVisit.visit_date))
    )
    anc_visits = anc_result.scalars().all()
    last_anc = anc_visits[0] if anc_visits else None

    # High risk flags
    flags = get_high_risk_flags(last_anc) if last_anc else []

    # BPCR
    bpcr_score = await get_bpcr_score(b.id, db)

    # Alerts
    alert_result = await db.execute(
        select(Alert)
        .where(Alert.beneficiary_id == b.id)
        .order_by(desc(Alert.triggered_at))
    )
    alerts = alert_result.scalars().all()

    # User mobile
    user_result = await db.execute(select(User).where(User.id == b.user_id))
    ben_user = user_result.scalar_one_or_none()

    next_due = last_anc.next_due_date if last_anc else None
    is_overdue = next_due is not None and next_due < date.today()

    return success_envelope({
        "id": str(b.id),
        "name": b.name,
        "age": b.age,
        "husband_name": b.husband_name,
        "mobile": ben_user.mobile if ben_user else None,
        "village": b.village,
        "block": b.block,
        "district": b.district,
        "address": b.address,
        "lmp": b.lmp.isoformat(),
        "edd": pregnancy["edd"].isoformat(),
        "blood_group": b.blood_group,
        "risk_level": b.risk_level.value if b.risk_level else "low",
        "gestational_week": pregnancy["gestational_week"],
        "trimester": pregnancy["trimester"],
        "days_until_edd": pregnancy["days_until_edd"],
        "bpcr_score": bpcr_score,
        "bpcr_risk": compute_bpcr_risk(bpcr_score) if bpcr_score is not None else None,
        "total_anc_visits": len(anc_visits),
        "last_anc_date": last_anc.visit_date.isoformat() if last_anc else None,
        "next_anc_due": next_due.isoformat() if next_due else None,
        "is_anc_overdue": is_overdue,
        "high_risk_flags": flags,
        "anc_visits": [{
            "id": str(v.id),
            "visit_date": v.visit_date.isoformat(),
            "visit_number": v.visit_number,
            "weight_kg": v.weight_kg,
            "bp_systolic": v.bp_systolic,
            "bp_diastolic": v.bp_diastolic,
            "hemoglobin": v.hemoglobin,
            "fetal_heart_rate": v.fetal_heart_rate,
            "next_due_date": v.next_due_date.isoformat() if v.next_due_date else None,
            "notes": v.notes,
        } for v in anc_visits],
        "alerts": [{
            "id": str(a.id),
            "alert_type": a.alert_type.value,
            "severity": a.severity.value,
            "status": a.status.value,
            "triggered_at": a.triggered_at.isoformat(),
        } for a in alerts],
        "created_at": b.created_at.isoformat() if b.created_at else None,
    })


@router.patch("/beneficiaries/{beneficiary_id}/risk", summary="Update beneficiary risk level")
async def update_risk_level(
    beneficiary_id: UUID,
    body: dict,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)
    result = await db.execute(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary")
    if b.asha_id != worker.id and b.anm_id != worker.id:
        raise ForbiddenException("Not assigned to you")

    risk = body.get("risk_level", "").lower()
    if risk not in ("low", "yellow", "red"):
        raise ValidationException("risk_level must be low, yellow or red")

    b.risk_level = RiskLevel(risk)
    await db.commit()
    return success_envelope({"message": "Risk level updated", "risk_level": risk})


# ── Alerts ─────────────────────────────────────────────────────────────────────

@router.get("/alerts", summary="List all alerts assigned to this worker")
async def list_alerts(
    status: Optional[str] = Query(None, description="Filter: pending|contacted|referred|resolved|closed"),
    severity: Optional[str] = Query(None, description="Filter: low|yellow|red"),
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    query = select(Alert).where(Alert.assigned_to == worker.id)

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

    # Pending first, then by triggered_at desc
    query = query.order_by(
        Alert.status == AlertStatus.pending,
        desc(Alert.triggered_at)
    )

    result = await db.execute(query)
    alerts = result.scalars().all()

    items = []
    now = datetime.now(timezone.utc)
    for alert in alerts:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if not ben:
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
            "beneficiary_name": ben.name,
            "beneficiary_village": ben.village,
            "beneficiary_mobile": ben_user.mobile if ben_user else None,
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "symptoms": alert.symptoms,
            "status": alert.status.value,
            "triggered_at": alert.triggered_at.isoformat(),
            "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
            "response_minutes": mins,
            "is_overdue": mins > 120 and alert.status == AlertStatus.pending,
        })

    return success_envelope({"alerts": items, "total": len(items)})


@router.get("/alerts/{alert_id}", summary="Get alert detail")
async def get_alert_detail(
    alert_id: UUID,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise NotFoundException("Alert")
    if alert.assigned_to != worker.id:
        raise ForbiddenException("This alert is not assigned to you")

    ben_res = await db.execute(
        select(Beneficiary).where(Beneficiary.id == alert.beneficiary_id)
    )
    ben = ben_res.scalar_one_or_none()
    user_res = await db.execute(select(User).where(User.id == ben.user_id))
    ben_user = user_res.scalar_one_or_none()

    triggered = alert.triggered_at
    now = datetime.now(timezone.utc)
    if triggered.tzinfo is None:
        triggered = triggered.replace(tzinfo=timezone.utc)
    mins = int((now - triggered).total_seconds() / 60)

    return success_envelope({
        "id": str(alert.id),
        "beneficiary_id": str(ben.id),
        "beneficiary_name": ben.name,
        "beneficiary_village": ben.village,
        "beneficiary_mobile": ben_user.mobile if ben_user else None,
        "beneficiary_address": ben.address,
        "alert_type": alert.alert_type.value,
        "severity": alert.severity.value,
        "symptoms": alert.symptoms,
        "notes": alert.notes,
        "status": alert.status.value,
        "triggered_at": alert.triggered_at.isoformat(),
        "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
        "closed_at": alert.closed_at.isoformat() if alert.closed_at else None,
        "response_minutes": mins,
        "is_overdue": mins > 120 and alert.status == AlertStatus.pending,
    })


@router.patch("/alerts/{alert_id}/status", summary="Update alert status")
async def update_alert_status(
    alert_id: UUID,
    payload: AlertStatusUpdate,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    """
    ASHA marks the alert as contacted / referred / resolved / closed.
    Sets responded_at on first status change from pending.
    Sets closed_at when status is resolved or closed.
    """
    worker = await get_worker_or_404(user, db)
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise NotFoundException("Alert")
    if alert.assigned_to != worker.id:
        raise ForbiddenException("This alert is not assigned to you")

    now = datetime.now(timezone.utc)

    # Set responded_at on first response
    if alert.status == AlertStatus.pending and not alert.responded_at:
        alert.responded_at = now

    # Set closed_at when resolving
    if payload.status in ("resolved", "closed"):
        alert.closed_at = now

    if payload.notes:
        alert.notes = (alert.notes or "") + f"\n[{now.strftime('%d/%m %H:%M')}] {payload.notes}"

    alert.status = AlertStatus(payload.status)
    await db.commit()

    return success_envelope({
        "message": f"Alert marked as {payload.status}",
        "alert_id": str(alert.id),
        "status": payload.status,
        "responded_at": alert.responded_at.isoformat() if alert.responded_at else None,
    })


# ── ANC Visits ─────────────────────────────────────────────────────────────────

@router.post("/anc-visits", summary="Record a new ANC visit for a beneficiary")
async def record_anc_visit(
    payload: ANCVisitCreate,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    # Verify beneficiary is assigned to this worker
    result = await db.execute(
        select(Beneficiary).where(Beneficiary.id == payload.beneficiary_id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary")
    if b.asha_id != worker.id and b.anm_id != worker.id:
        raise ForbiddenException("Beneficiary not assigned to you")

    # Auto-set visit number
    count_res = await db.execute(
        select(func.count(ANCVisit.id))
        .where(ANCVisit.beneficiary_id == b.id)
    )
    visit_count = count_res.scalar() or 0

    from app.models.models import ANCVisit as ANCVisitModel
    visit = ANCVisitModel(
        beneficiary_id=payload.beneficiary_id,
        worker_id=worker.id,
        visit_date=payload.visit_date,
        visit_number=payload.visit_number or (visit_count + 1),
        weight_kg=payload.weight_kg,
        bp_systolic=payload.bp_systolic,
        bp_diastolic=payload.bp_diastolic,
        hemoglobin=payload.hemoglobin,
        fundal_height=payload.fundal_height,
        fetal_heart_rate=payload.fetal_heart_rate,
        notes=payload.notes,
        next_due_date=payload.next_due_date,
    )
    db.add(visit)

    # Auto-update risk level based on vitals
    flags = get_high_risk_flags(visit)
    if flags:
        if "severe_anemia" in flags or (visit.bp_systolic and visit.bp_systolic > 160):
            b.risk_level = RiskLevel.red
        elif b.risk_level == RiskLevel.low:
            b.risk_level = RiskLevel.yellow

    await db.commit()
    await db.refresh(visit)

    return success_envelope({
        "message": "ANC visit recorded successfully",
        "visit_id": str(visit.id),
        "visit_number": visit.visit_number,
        "next_due_date": visit.next_due_date.isoformat() if visit.next_due_date else None,
        "high_risk_flags": flags,
        "risk_level_updated": b.risk_level.value,
    })


@router.get("/anc-visits/{beneficiary_id}", summary="ANC visit history for a beneficiary")
async def get_anc_history(
    beneficiary_id: UUID,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    result = await db.execute(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary")
    if b.asha_id != worker.id and b.anm_id != worker.id:
        raise ForbiddenException("Not assigned to you")

    anc_result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == beneficiary_id)
        .order_by(desc(ANCVisit.visit_date))
    )
    visits = anc_result.scalars().all()

    return success_envelope({
        "beneficiary_id": str(beneficiary_id),
        "beneficiary_name": b.name,
        "total_visits": len(visits),
        "visits": [{
            "id": str(v.id),
            "visit_date": v.visit_date.isoformat(),
            "visit_number": v.visit_number,
            "weight_kg": v.weight_kg,
            "bp_systolic": v.bp_systolic,
            "bp_diastolic": v.bp_diastolic,
            "hemoglobin": v.hemoglobin,
            "fundal_height": v.fundal_height,
            "fetal_heart_rate": v.fetal_heart_rate,
            "notes": v.notes,
            "next_due_date": v.next_due_date.isoformat() if v.next_due_date else None,
            "high_risk_flags": get_high_risk_flags(v),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        } for v in visits],
    })


# ── BPCR by ASHA ───────────────────────────────────────────────────────────────

@router.post("/bpcr", summary="Submit BPCR assessment for a beneficiary")
async def submit_bpcr(
    payload: ASHABPCRRequest,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    result = await db.execute(
        select(Beneficiary).where(Beneficiary.id == payload.beneficiary_id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary")
    if b.asha_id != worker.id and b.anm_id != worker.id:
        raise ForbiddenException("Not assigned to you")

    for item in payload.responses:
        component = item.get("component")
        if component not in BPCR_COMPONENTS:
            continue
        assessment = BPCRAssessment(
            beneficiary_id=b.id,
            assessed_by=user.id,
            component=component,
            score=item.get("score", 0),
            response=item.get("response", {}),
        )
        db.add(assessment)

    await db.commit()

    total_score = await get_bpcr_score(b.id, db)

    # Update risk level if BPCR is low
    from app.core.config import settings
    if total_score is not None and total_score < settings.BPCR_ALERT_THRESHOLD:
        if b.risk_level == RiskLevel.low:
            b.risk_level = RiskLevel.yellow
            await db.commit()

    return success_envelope({
        "message": "BPCR assessment saved",
        "total_score": total_score,
        "risk_label": compute_bpcr_risk(total_score) if total_score is not None else "Unknown",
    })


# ── Quick filters ──────────────────────────────────────────────────────────────

@router.get("/overdue-anc", summary="List women with overdue ANC visits")
async def get_overdue_anc(
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    ben_result = await db.execute(
        select(Beneficiary).where(Beneficiary.asha_id == worker.id)
    )
    beneficiaries = ben_result.scalars().all()

    overdue = []
    for b in beneficiaries:
        anc_res = await db.execute(
            select(ANCVisit)
            .where(ANCVisit.beneficiary_id == b.id)
            .order_by(desc(ANCVisit.visit_date))
            .limit(1)
        )
        last_anc = anc_res.scalar_one_or_none()
        if last_anc and last_anc.next_due_date and last_anc.next_due_date < date.today():
            days_overdue = (date.today() - last_anc.next_due_date).days
            user_res = await db.execute(select(User).where(User.id == b.user_id))
            ben_user = user_res.scalar_one_or_none()
            overdue.append({
                "beneficiary_id": str(b.id),
                "name": b.name,
                "village": b.village,
                "mobile": ben_user.mobile if ben_user else None,
                "last_anc_date": last_anc.visit_date.isoformat(),
                "due_date": last_anc.next_due_date.isoformat(),
                "days_overdue": days_overdue,
                "risk_level": b.risk_level.value if b.risk_level else "low",
            })

    overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
    return success_envelope({"overdue": overdue, "total": len(overdue)})


@router.get("/high-risk", summary="List high-risk beneficiaries (red and yellow)")
async def get_high_risk(
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    worker = await get_worker_or_404(user, db)

    result = await db.execute(
        select(Beneficiary)
        .where(Beneficiary.asha_id == worker.id)
        .where(Beneficiary.risk_level.in_([RiskLevel.red, RiskLevel.yellow]))
        .order_by(Beneficiary.risk_level.desc())
    )
    beneficiaries = result.scalars().all()

    items = []
    for b in beneficiaries:
        item = await build_beneficiary_list_item(b, db)
        items.append(item)

    return success_envelope({"high_risk": items, "total": len(items)})
 
# ── Facility Location ──────────────────────────────────────────────────────────
@router.get("/facilities", summary="Facility location list with distance",
            operation_id="asha_facilities")
async def get_facilities(
    latitude: Optional[float] = Query(None, description="Current latitude for distance calc"),
    longitude: Optional[float] = Query(None, description="Current longitude"),
    facility_type: Optional[str] = Query(None, description="MC|DH|SDH|CHC|PHC|SHC"),
    has_labour_room: Optional[bool] = Query(None),
    is_24x7: Optional[bool] = Query(None),
    is_functional: Optional[bool] = Query(True),
    district: Optional[str] = Query(None),
    sub_district: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by facility name"),
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import HealthFacility
    from sqlalchemy import select

    worker = await get_worker_or_404(user, db)

    # Build query
    query = select(HealthFacility)

    if facility_type:
        query = query.where(HealthFacility.facility_type == facility_type.upper())
    if has_labour_room is not None:
        query = query.where(HealthFacility.has_labour_room == has_labour_room)
    if is_24x7 is not None:
        query = query.where(HealthFacility.is_24x7 == is_24x7)
    if is_functional is not None:
        query = query.where(HealthFacility.is_functional == is_functional)
    if district:
        query = query.where(HealthFacility.district.ilike(f"%{district}%"))
    if sub_district:
        query = query.where(HealthFacility.sub_district.ilike(f"%{sub_district}%"))
    if search:
        query = query.where(HealthFacility.name.ilike(f"%{search}%"))

    result = await db.execute(query)
    facilities = result.scalars().all()

    # Use worker coords as fallback
    ref_lat = latitude or (float(worker.latitude) if worker.latitude else None)
    ref_lon = longitude or (float(worker.longitude) if worker.longitude else None)

    items = []
    for f in facilities:
        distance_km = None
        estimated_time_min = None

        if ref_lat and ref_lon and f.latitude and f.longitude:
            try:
                dist = haversine_km(ref_lat, ref_lon, float(f.latitude), float(f.longitude))
                distance_km = round(dist, 1)
                estimated_time_min = int(dist * 3)  # ~20 km/h rural speed
            except (ValueError, TypeError):
                pass

        items.append({
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
            "distance_km": distance_km,
            "estimated_time_min": estimated_time_min,
        })

    # Sort by distance if coords available, else by type priority
    type_order = {"MC": 1, "DH": 2, "SDH": 3, "CHC": 4, "PHC": 5, "SHC": 6}
    if ref_lat and ref_lon:
        items.sort(key=lambda x: x["distance_km"] or 999)
    else:
        items.sort(key=lambda x: (type_order.get(x["facility_type"], 9), x["name"]))

    return success_envelope({
        "facilities": items,
        "total": len(items),
        "nearest": items[0] if items else None,
        "reference_location": {"latitude": ref_lat, "longitude": ref_lon},
        "emergency_numbers": {
            "ambulance_102": "102",
            "ambulance_108": "108",
            "women_helpline": "181",
        },
        "filters_applied": {
            "facility_type": facility_type,
            "has_labour_room": has_labour_room,
            "is_24x7": is_24x7,
            "district": district,
            "search": search,
        },
    })

# ── Safety / Accountability Screen ────────────────────────────────────────────
 
@router.get("/accountability", summary="Safety and accountability — response time tracking",
            operation_id="asha_accountability")
async def get_accountability(
    days: int = Query(30, description="Last N days"),
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    """
    Accountability screen from wireframe:
    - Response time tracking
    - Action taken (in-progress / closed)
    - Graph data for response time trend
    """
    worker = await get_worker_or_404(user, db)
    since = datetime.now(timezone.utc) - timedelta(days=days)
 
    # All alerts assigned to this worker
    result = await db.execute(
        select(Alert)
        .where(Alert.assigned_to == worker.id)
        .where(Alert.triggered_at >= since)
        .order_by(desc(Alert.triggered_at))
    )
    alerts = result.scalars().all()
 
    now = datetime.now(timezone.utc)
    total = len(alerts)
    responded = [a for a in alerts if a.responded_at]
    in_progress = [a for a in alerts if a.status.value in ("contacted", "referred")]
    closed = [a for a in alerts if a.status.value in ("resolved", "closed")]
    pending = [a for a in alerts if a.status == AlertStatus.pending]
 
    response_times = []
    sla_minutes = 120  # 2 hours SLA
    within_sla = 0
    delayed = 0
 
    for a in responded:
        triggered = a.triggered_at
        resp = a.responded_at
        if triggered.tzinfo is None:
            triggered = triggered.replace(tzinfo=timezone.utc)
        if resp.tzinfo is None:
            resp = resp.replace(tzinfo=timezone.utc)
        mins = int((resp - triggered).total_seconds() / 60)
        response_times.append({
            "alert_id": str(a.id),
            "response_minutes": mins,
            "within_sla": mins <= sla_minutes,
            "date": a.triggered_at.date().isoformat(),
        })
        if mins <= sla_minutes:
            within_sla += 1
        else:
            delayed += 1
 
    avg_response = round(
        sum(r["response_minutes"] for r in response_times) / len(response_times), 1
    ) if response_times else 0
 
    pct_within_sla = round(within_sla / len(responded) * 100, 1) if responded else 0
 
    # Daily response trend for graph
    trend_by_day: dict = {}
    for r in response_times:
        d = r["date"]
        if d not in trend_by_day:
            trend_by_day[d] = {"times": [], "count": 0}
        trend_by_day[d]["times"].append(r["response_minutes"])
        trend_by_day[d]["count"] += 1
 
    trend = [
        {
            "date": k,
            "avg_response_minutes": round(sum(v["times"]) / len(v["times"]), 1),
            "alerts_count": v["count"],
        }
        for k, v in sorted(trend_by_day.items())
    ]
 
    # Action breakdown
    action_breakdown = [
        {"status": "Pending", "count": len(pending), "color": "red"},
        {"status": "In Progress", "count": len(in_progress), "color": "yellow"},
        {"status": "Closed", "count": len(closed), "color": "green"},
    ]
 
    # Recent closed alerts
    recent_closed = []
    for a in closed[-5:]:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == a.beneficiary_id)
        )
        ben = ben_res.scalar_one_or_none()
        if ben:
            triggered = a.triggered_at
            if triggered.tzinfo is None:
                triggered = triggered.replace(tzinfo=timezone.utc)
            resp = a.responded_at
            if resp and resp.tzinfo is None:
                resp = resp.replace(tzinfo=timezone.utc)
            recent_closed.append({
                "alert_id": str(a.id),
                "beneficiary_name": ben.name,
                "village": ben.village,
                "severity": a.severity.value,
                "triggered_at": a.triggered_at.isoformat(),
                "closed_at": a.closed_at.isoformat() if a.closed_at else None,
                "response_minutes": int((resp - triggered).total_seconds() / 60) if resp else None,
            })
 
    return success_envelope({
        "summary": {
            "total_alerts": total,
            "responded": len(responded),
            "pending": len(pending),
            "in_progress": len(in_progress),
            "closed": len(closed),
            "avg_response_minutes": avg_response,
            "pct_within_sla": pct_within_sla,
            "sla_threshold_minutes": sla_minutes,
            "delayed_cases": delayed,
        },
        "action_breakdown": action_breakdown,
        "response_trend": trend,
        "recent_closed": recent_closed,
        "period_days": days,
        "chart_type": "line",
        "guidelines": {
            "hi": "SLA नियम: हर खतरे के अलर्ट पर 2 घंटे के अंदर प्रतिक्रिया दें",
            "en": "SLA rule: Respond to every danger alert within 2 hours",
        },
    })
 
 
# ── Visit Log ──────────────────────────────────────────────────────────────────
 
@router.post("/visit-log", summary="Log a home visit or activity for a beneficiary",
             operation_id="asha_visit_log_create")
async def create_visit_log(
    body: dict,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    """
    ASHA logs a home visit or activity.
    Stored in audit_logs table for activity tracking.
    """
    from app.models.models import AuditLog
    worker = await get_worker_or_404(user, db)
 
    beneficiary_id = body.get("beneficiary_id")
    visit_type = body.get("visit_type", "home_visit")
    notes = body.get("notes", "")
    status = body.get("status", "completed")
 
    valid_types = ["home_visit", "anc_counselling", "bpcr_counselling",
                   "medicine_distribution", "follow_up", "emergency_response", "other"]
    if visit_type not in valid_types:
        visit_type = "other"
 
    # Verify beneficiary belongs to this worker
    if beneficiary_id:
        ben_res = await db.execute(
            select(Beneficiary).where(Beneficiary.id == UUID(str(beneficiary_id)))
        )
        ben = ben_res.scalar_one_or_none()
        if not ben:
            raise NotFoundException("Beneficiary")
 
    # Store as audit log entry
    log = AuditLog(
        user_id=user.id,
        action=f"visit.{visit_type}",
        resource="beneficiary",
        resource_id=UUID(str(beneficiary_id)) if beneficiary_id else None,
        metadata={
            "visit_type": visit_type,
            "notes": notes,
            "status": status,
            "worker_id": str(worker.id),
            "worker_role": worker.worker_role,
            "visit_date": date.today().isoformat(),
        },
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
 
    return success_envelope({
        "message": "Visit logged successfully",
        "log_id": str(log.id),
        "visit_type": visit_type,
        "visit_date": date.today().isoformat(),
        "beneficiary_id": str(beneficiary_id) if beneficiary_id else None,
    })
 
 
@router.get("/visit-log/{beneficiary_id}", summary="Get visit log history for a beneficiary",
            operation_id="asha_visit_log_get")
async def get_visit_log(
    beneficiary_id: UUID,
    user: User = Depends(get_current_field_worker),
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import AuditLog
    worker = await get_worker_or_404(user, db)
 
    ben_res = await db.execute(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id)
    )
    ben = ben_res.scalar_one_or_none()
    if not ben:
        raise NotFoundException("Beneficiary")
 
    if ben.asha_id != worker.id and ben.anm_id != worker.id:
        from app.core.exceptions import ForbiddenException
        raise ForbiddenException("Not assigned to you")
 
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.resource == "beneficiary")
        .where(AuditLog.resource_id == beneficiary_id)
        .where(AuditLog.action.like("visit.%"))
        .order_by(desc(AuditLog.created_at))
    )
    logs = result.scalars().all()
 
    return success_envelope({
        "beneficiary_id": str(beneficiary_id),
        "beneficiary_name": ben.name,
        "total_visits": len(logs),
        "visit_log": [{
            "id": str(log.id),
            "visit_type": log.metadata.get("visit_type") if log.metadata else "unknown",
            "notes": log.metadata.get("notes") if log.metadata else "",
            "status": log.metadata.get("status") if log.metadata else "completed",
            "visit_date": log.metadata.get("visit_date") if log.metadata else None,
            "logged_at": log.created_at.isoformat() if log.created_at else None,
        } for log in logs],
    })
 