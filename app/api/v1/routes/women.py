"""
Women App Routes
================
POST   /api/v1/women/register               → Self-register after OTP
GET    /api/v1/women/profile                → Get own profile + pregnancy info
PATCH  /api/v1/women/profile                → Update profile
GET    /api/v1/women/pregnancy/current      → Current pregnancy details
GET    /api/v1/women/pregnancy/week/:n      → Week-by-week guide

GET    /api/v1/women/bpcr                   → BPCR scores + summary
POST   /api/v1/women/bpcr/respond           → Submit BPCR component responses

GET    /api/v1/women/anc-services           → ANC visits + overdue info + week guide
GET    /api/v1/women/appointments           → Upcoming appointments
POST   /api/v1/women/appointments           → Book appointment

GET    /api/v1/women/reminders              → Pending/upcoming reminders

GET    /api/v1/women/schemes                → List govt schemes
GET    /api/v1/women/schemes/eligibility    → Check eligibility for all schemes
GET    /api/v1/women/schemes/:id            → Scheme detail

GET    /api/v1/women/faqs                   → FAQs list (bilingual)
GET    /api/v1/women/chatbot/history        → Conversation history
POST   /api/v1/women/chatbot/message        → Send message to AI chatbot
POST   /api/v1/women/chatbot/feedback       → Rate a chatbot response
DELETE /api/v1/women/chatbot/history        → Clear conversation

POST   /api/v1/women/danger-sign            → Report danger sign → alert
POST   /api/v1/women/emergency              → Full emergency: alert + notify ASHA + 102/108
GET    /api/v1/women/emergency/contacts     → Emergency contact list

Additional Routes for women health
=====================================================
GET  /api/v1/women/symptom-checker              → Symptom checker with guidance
GET  /api/v1/women/nutrition                    → Nutrition counselling by trimester
GET  /api/v1/women/lifestyle                    → Lifestyle & activity guidance
GET  /api/v1/women/health-dashboard             → Personal health dashboard
GET  /api/v1/women/tests                        → Test & investigation tracker
POST /api/v1/women/tests                        → Log a test result
GET  /api/v1/women/reminders/medicine           → Medicine reminders
POST /api/v1/women/reminders/medicine           → Add medicine reminder
GET  /api/v1/women/postnatal                    → Postnatal care guide
GET  /api/v1/women/newborn-care                 → Newborn care guide
"""

from datetime import date, datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    success_envelope,
)
from app.models.models import (
    User, Beneficiary, BPCRAssessment, ANCVisit,
    Appointment, Reminder, Alert, AlertType, RiskLevel,
    EducationalContent, ChatbotConversation,FAQ,
    PregnancyRegistration, MedicineTracker, Immunization, UltrasoundScan,
)
from app.schemas.women import (
    WomenRegisterRequest,
    WomenProfileUpdate,
    BeneficiaryOut,
    PregnancyInfo,
    BPCRRespondRequest,
    BPCRSummary,
    BPCR_COMPONENTS,
    ANCServicesResponse,
    ANCVisitOut,
    AppointmentCreate,
    AppointmentOut,
    ReminderOut,
    DangerSignRequest,
    EmergencyRequest,
    EmergencyResponse,
    ChatbotMessageRequest,
    ChatbotMessageResponse,
    ChatbotFeedbackRequest,
    SchemeEligibilityResponse,
    FAQOut,
    ANC_VISIT_TEMPLATE, MEDICINE_DEFAULTS, IMMUNIZATION_DOSE_TYPES, ULTRASOUND_SCAN_TYPES,   # NEW
    ImmunizationUpdateRequest, UltrasoundUpdateRequest,ChecklistUpdateRequest,RegistrationFieldUpdateRequest, MedicineDateToggleRequest
)
from app.api.v1.dependencies import get_current_woman, get_current_user
from app.services.notification_service import send_fcm_push, send_alert_to_asha
from app.services.chatbot_service import get_ai_reply

router = APIRouter(prefix="/women", tags=["Beneficiary (Pregnant Women)"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def compute_pregnancy_info(lmp: date) -> dict:
    today = date.today()
    days_pregnant = (today - lmp).days
    gestational_week = min(days_pregnant // 7, 42)
    trimester = 1 if gestational_week < 14 else (2 if gestational_week < 28 else 3)
    edd = lmp + timedelta(days=280)
    days_until_edd = (edd - today).days
    return {
        "lmp": lmp,
        "edd": edd,
        "gestational_week": gestational_week,
        "trimester": trimester,
        "days_until_edd": max(0, days_until_edd),
    }


def compute_bpcr_risk(score: int) -> str:
    if score >= 8:
        return "Green"
    elif score >= 5:
        return "Yellow"
    return "Red"


async def get_beneficiary_or_404(user: User, db: AsyncSession) -> Beneficiary:
    result = await db.execute(
        select(Beneficiary)
        .where(Beneficiary.user_id == user.id)
    )
    b = result.scalar_one_or_none()
    if not b:
        raise NotFoundException("Beneficiary profile")
    return b

# ── Lazy-provisioning helpers (auto-create default rows on first access) ──────

async def get_or_create_registration(beneficiary_id: UUID, db: AsyncSession) -> PregnancyRegistration:
    result = await db.execute(
        select(PregnancyRegistration).where(PregnancyRegistration.beneficiary_id == beneficiary_id)
    )
    reg = result.scalar_one_or_none()
    if not reg:
        reg = PregnancyRegistration(beneficiary_id=beneficiary_id)
        db.add(reg)
        await db.commit()
        await db.refresh(reg)
    return reg


async def get_or_create_medicine_trackers(beneficiary_id: UUID, db: AsyncSession) -> dict[str, MedicineTracker]:
    result = await db.execute(select(MedicineTracker).where(MedicineTracker.beneficiary_id == beneficiary_id))
    existing = {t.medicine_type: t for t in result.scalars().all()}
    created = False
    for med_type, total in MEDICINE_DEFAULTS.items():
        if med_type not in existing:
            t = MedicineTracker(beneficiary_id=beneficiary_id, medicine_type=med_type, total_doses=total)
            db.add(t)
            existing[med_type] = t
            created = True
    if created:
        await db.commit()
        for t in existing.values():
            await db.refresh(t)
    return existing


async def get_or_create_immunizations(beneficiary_id: UUID, db: AsyncSession) -> dict[str, Immunization]:
    result = await db.execute(select(Immunization).where(Immunization.beneficiary_id == beneficiary_id))
    existing = {i.dose_type: i for i in result.scalars().all()}
    created = False
    for dose_type in IMMUNIZATION_DOSE_TYPES:
        if dose_type not in existing:
            i = Immunization(beneficiary_id=beneficiary_id, dose_type=dose_type)
            db.add(i)
            existing[dose_type] = i
            created = True
    if created:
        await db.commit()
        for i in existing.values():
            await db.refresh(i)
    return existing


async def get_or_create_ultrasounds(beneficiary_id: UUID, db: AsyncSession) -> dict[str, UltrasoundScan]:
    result = await db.execute(select(UltrasoundScan).where(UltrasoundScan.beneficiary_id == beneficiary_id))
    existing = {s.scan_type: s for s in result.scalars().all()}
    created = False
    for scan_type in ULTRASOUND_SCAN_TYPES:
        if scan_type not in existing:
            s = UltrasoundScan(beneficiary_id=beneficiary_id, scan_type=scan_type)
            db.add(s)
            existing[scan_type] = s
            created = True
    if created:
        await db.commit()
        for s in existing.values():
            await db.refresh(s)
    return existing

def get_week(lmp: date) -> int:
    return min((date.today() - lmp).days // 7, 42)

def get_trimester(lmp: date) -> int:
    weeks = (date.today() - lmp).days // 7
    return 1 if weeks < 14 else (2 if weeks < 28 else 3)

# ── Registration ───────────────────────────────────────────────────────────────

@router.post("/register", summary="Self-register as a pregnant woman")
async def register_woman(
    payload: WomenRegisterRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called after OTP verification when `is_new_user=True`.
    Creates the beneficiary record linked to the authenticated user.
    """
    existing = await db.execute(select(Beneficiary).where(Beneficiary.user_id == user.id))
    if existing.scalar_one_or_none():
        raise ConflictException("Registration already completed. Use PATCH /women/profile to update.")

    user.name = payload.name
    user.preferred_language = payload.preferred_language  # type: ignore

    beneficiary = Beneficiary(
        user_id=user.id,
        name=payload.name,
        age=payload.age,
        husband_name=payload.husband_name,
        husband_age=payload.husband_age,
        dob=payload.dob,
        address=payload.address,
        village=payload.village,
        phc=payload.phc,
        block=payload.block,
        district=payload.district,
        lmp=payload.lmp,
        blood_group=payload.blood_group,
        consent=payload.consent,
        consent_at=datetime.now(timezone.utc) if payload.consent else None,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.add(beneficiary)
    await db.commit()
    await db.refresh(beneficiary)

    pregnancy = compute_pregnancy_info(payload.lmp)

    return success_envelope({
        "message": "Registration successful",
        "beneficiary_id": str(beneficiary.id),
        "pregnancy": pregnancy,
    })


# ── Profile ────────────────────────────────────────────────────────────────────

@router.get("/profile", summary="Get own profile + pregnancy summary", operation_id="women_get_profile")
async def get_profile(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    pregnancy = compute_pregnancy_info(b.lmp)

    from datetime import timedelta
    profile_data = {
        "id": str(b.id),
        "name": b.name,
        "age": b.age,
        "husband_name": b.husband_name,
        "village": b.village,
        "block": b.block,
        "district": b.district,
        "lmp": b.lmp.isoformat() if b.lmp else None,
        "edd": (b.lmp + timedelta(days=280)).isoformat() if b.lmp else None,
        "blood_group": b.blood_group,
        "risk_level": b.risk_level.value if b.risk_level else "low",
        "preferred_language": user.preferred_language.value if user.preferred_language else "hi",
        "asha_name": None,
        "anm_name": None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }

    return success_envelope({
        "user": {
            "id": str(user.id),
            "mobile": user.mobile,
            "name": user.name,
            "preferred_language": user.preferred_language.value if user.preferred_language else "hi",
        },
        "profile": profile_data,
        "pregnancy": pregnancy,
    })

@router.patch("/profile", summary="Update profile details")
async def update_profile(
    payload: WomenProfileUpdate,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    update_data = payload.model_dump(exclude_unset=True)

    # FCM token goes on User; everything else on Beneficiary
    if "fcm_token" in update_data:
        user.fcm_token = update_data.pop("fcm_token")
    if "preferred_language" in update_data:
        user.preferred_language = update_data["preferred_language"]  # type: ignore

    for field, value in update_data.items():
        setattr(b, field, value)

    await db.commit()
    await db.refresh(b)
    return success_envelope({"message": "Profile updated", "profile": BeneficiaryOut.model_validate(b).model_dump()})


# ── Pregnancy ──────────────────────────────────────────────────────────────────

@router.get("/pregnancy/current", summary="Current pregnancy details")
async def get_pregnancy(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    return success_envelope(compute_pregnancy_info(b.lmp))


@router.get("/pregnancy/week/{week_number}", summary="Week-by-week pregnancy guide")
async def get_week_guide(
    week_number: int,
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
):
    """Returns developmental info, nutrition tips, warning signs for a given gestational week."""
    if not 1 <= week_number <= 42:
        raise ValidationException("Week number must be between 1 and 42")

    # In production, load from educational_content table or a static fixture
    trimester = 1 if week_number < 14 else (2 if week_number < 28 else 3)
    return success_envelope({
        "week": week_number,
        "trimester": trimester,
        "baby_development": f"Week {week_number} baby development content (load from DB)",
        "mother_changes": f"Week {week_number} mother body changes (load from DB)",
        "nutrition_tips": ["Iron-rich foods", "Stay hydrated", "Take folic acid"],
        "warning_signs": ["Heavy bleeding", "Severe headache", "Reduced fetal movement"],
        "what_to_expect": f"General week {week_number} expectations",
    })


# ── BPCR ───────────────────────────────────────────────────────────────────────

@router.get("/bpcr", summary="Get BPCR assessment scores and summary")
async def get_bpcr(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.beneficiary_id == b.id)
        .order_by(desc(BPCRAssessment.assessed_at))
    )
    assessments = result.scalars().all()

    # Latest score per component
    seen: dict[str, BPCRAssessment] = {}
    for a in assessments:
        if a.component not in seen:
            seen[a.component] = a

    total_score = sum(a.score or 0 for a in seen.values())
    completed = list(seen.keys())
    missing = [c for c in BPCR_COMPONENTS if c not in seen]
    last_at = max((a.assessed_at for a in seen.values()), default=None) if seen else None

    return success_envelope(BPCRSummary(
        total_score=total_score,
        percentage=round(total_score / 10 * 100, 1),
        risk_label=compute_bpcr_risk(total_score),
        completed_components=completed,
        missing_components=missing,
        last_assessed_at=last_at,
        components=[
            {
                "component": k,
                "score": v.score,
                "response": v.response,
                "assessed_at": v.assessed_at.isoformat(),
            }
            for k, v in seen.items()
        ],
    ).model_dump())


@router.post("/bpcr/respond", summary="Submit responses to BPCR components")
async def respond_bpcr(
    payload: BPCRRespondRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    """
    Women can self-assess BPCR components.
    Each submission upserts the component score.
    Triggers ASHA alert if total score < BPCR_ALERT_THRESHOLD.
    """
    b = await get_beneficiary_or_404(user, db)

    for item in payload.responses:
        assessment = BPCRAssessment(
            beneficiary_id=b.id,
            assessed_by=user.id,
            component=item.component,
            score=item.score,
            response=item.response,
        )
        db.add(assessment)

    await db.commit()

    # Re-fetch total score
    result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.beneficiary_id == b.id)
        .order_by(desc(BPCRAssessment.assessed_at))
    )
    all_assessments = result.scalars().all()
    seen: dict[str, BPCRAssessment] = {}
    for a in all_assessments:
        if a.component not in seen:
            seen[a.component] = a
    total_score = sum(a.score or 0 for a in seen.values())

    # Alert if score below threshold
    from app.core.config import settings
    triggered_alert = None
    if total_score < settings.BPCR_ALERT_THRESHOLD:
        alert = Alert(
            beneficiary_id=b.id,
            triggered_by=user.id,
            alert_type=AlertType.bpcr_low,
            severity=RiskLevel.yellow,
            symptoms={},
            notes=f"Low BPCR score: {total_score}/10",
            assigned_to=b.asha_id,
        )
        db.add(alert)
        await db.commit()
        triggered_alert = str(alert.id)

    return success_envelope({
        "message": "BPCR responses saved",
        "total_score": total_score,
        "risk_label": compute_bpcr_risk(total_score),
        "alert_triggered": triggered_alert is not None,
        "alert_id": triggered_alert,
    })


# ── ANC Services ───────────────────────────────────────────────────────────────
@router.get("/anc-services", summary="Full ANC Services screen data")
async def get_anc_services(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    pregnancy = compute_pregnancy_info(b.lmp)

    result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .order_by(desc(ANCVisit.visit_date))
    )
    visits = result.scalars().all()

    # Latest ANCVisit row per visit_number (1-4), for merging into the template.
    visit_by_number: dict[int, ANCVisit] = {}
    for v in visits:
        if v.visit_number and v.visit_number not in visit_by_number:
            visit_by_number[v.visit_number] = v

    last_visit = visits[0] if visits else None
    next_due = last_visit.next_due_date if last_visit else None
    today = date.today()
    is_overdue = next_due is not None and next_due < today
    overdue_days = (today - next_due).days if is_overdue else None

    high_risk_flags = []
    if last_visit:
        if last_visit.bp_systolic and last_visit.bp_systolic > 140:
            high_risk_flags.append("high_bp")
        if last_visit.bp_diastolic and last_visit.bp_diastolic > 90:
            high_risk_flags.append("high_diastolic_bp")
        if last_visit.hemoglobin:
            hb = float(last_visit.hemoglobin)
            if hb < 7.0:
                high_risk_flags.append("severe_anemia")
            elif hb < 11.0:
                high_risk_flags.append("mild_anemia")

    # ── Merge template + actual checklist state into a per-visit timeline ──
    timeline = []
    for visit_number, template in ANC_VISIT_TEMPLATE.items():
        visit = visit_by_number.get(visit_number)
        checklist_state = (visit.checklist or {}) if visit else {}
        items = [{
            "key": key,
            "label_hi": label_hi,
            "label_en": label_en,
            "checked": bool(checklist_state.get(key, False)),
        } for key, label_hi, label_en in template["items"]]
        checked_count = sum(1 for i in items if i["checked"])
        timeline.append({
            "visit_number": visit_number,
            "title_hi": template["title_hi"],
            "title_en": template["title_en"],
            "week_range": template["week_range"],
            "items": items,
            "tests_completed": checked_count,
            "tests_total": len(items),
            "status": "completed" if items and checked_count == len(items) else "due",
            "visit_date": visit.visit_date.isoformat() if visit else None,
        })

    # ── BPCR percentage (for the header card) ──
    bpcr_result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.beneficiary_id == b.id)
        .order_by(desc(BPCRAssessment.assessed_at))
    )
    seen_bpcr: dict[str, BPCRAssessment] = {}
    for a in bpcr_result.scalars().all():
        if a.component not in seen_bpcr:
            seen_bpcr[a.component] = a
    bpcr_score_percent = round(sum(a.score or 0 for a in seen_bpcr.values()) / 10 * 100, 0) if seen_bpcr else None

    reg = await get_or_create_registration(b.id, db)
    trackers = await get_or_create_medicine_trackers(b.id, db)
    immunizations = await get_or_create_immunizations(b.id, db)
    scans = await get_or_create_ultrasounds(b.id, db)

    return success_envelope({
        "gestational_week": pregnancy["gestational_week"],
        "trimester": pregnancy["trimester"],
        "edd": pregnancy["edd"].isoformat(),
        "high_risk_status": "Yes" if high_risk_flags else "No",
        "high_risk_flags": high_risk_flags,
        "bpcr_score_percent": bpcr_score_percent,
        "visit_progress": [
            {"visit_number": t["visit_number"], "week_range": t["week_range"], "is_completed": t["status"] == "completed"}
            for t in timeline
        ],
        "pregnancy_registration": {
            "is_registered": reg.is_registered,
            "registered_date": reg.registered_date.isoformat() if reg.registered_date else None,
            "rch_id_generated": bool(reg.rch_id),
            "rch_id": reg.rch_id,
            "mcp_card_received": reg.mcp_card_received,
            "asha_assigned": b.asha_id is not None,
        },
        "anc_visit_timeline": timeline,
        "medicine_tracker": {
            med_type: {"taken": t.doses_taken, "total": t.total_doses}
            for med_type, t in trackers.items()
        },
        "immunization": [{
            "dose_type": dose_type,
            "status": immunizations[dose_type].status,
            "date": immunizations[dose_type].received_date.isoformat() if immunizations[dose_type].received_date else None,
        } for dose_type in IMMUNIZATION_DOSE_TYPES],
        "ultrasound": [{
            "scan_type": scan_type,
            "status": scans[scan_type].status,
            "scan_date": scans[scan_type].scan_date.isoformat() if scans[scan_type].scan_date else None,
        } for scan_type in ULTRASOUND_SCAN_TYPES],
        "visits": [ANCVisitOut.model_validate(v).model_dump() for v in visits],
        "total_visits": len(visits),
        "last_visit": ANCVisitOut.model_validate(last_visit).model_dump() if last_visit else None,
        "next_due_date": next_due.isoformat() if next_due else None,
        "is_overdue": is_overdue,
        "overdue_days": overdue_days,
    })


@router.post("/anc-services/medicine/{medicine_type}/mark-taken", summary="Mark today's dose as taken")
async def mark_medicine_taken(
    medicine_type: str,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if medicine_type not in MEDICINE_DEFAULTS:
        raise ValidationException("Invalid medicine type. Use 'iron' or 'calcium'")
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(MedicineTracker)
        .where(MedicineTracker.beneficiary_id == b.id)
        .where(MedicineTracker.medicine_type == medicine_type)
    )
    tracker = result.scalar_one_or_none()
    if not tracker:
        tracker = MedicineTracker(beneficiary_id=b.id, medicine_type=medicine_type, total_doses=MEDICINE_DEFAULTS[medicine_type])
        db.add(tracker)

    today = date.today()
    if tracker.last_taken_date == today:
        return success_envelope({
            "message": "Already marked for today",
            "medicine_type": medicine_type,
            "doses_taken": tracker.doses_taken,
            "total_doses": tracker.total_doses,
        })

    tracker.doses_taken = min(tracker.doses_taken + 1, tracker.total_doses)
    tracker.last_taken_date = today
    await db.commit()
    await db.refresh(tracker)

    return success_envelope({
        "message": "Dose marked as taken",
        "medicine_type": medicine_type,
        "doses_taken": tracker.doses_taken,
        "total_doses": tracker.total_doses,
    })


@router.patch("/anc-services/immunization/{dose_type}", summary="Update immunization dose status")
async def update_immunization(
    dose_type: str,
    payload: ImmunizationUpdateRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if dose_type not in IMMUNIZATION_DOSE_TYPES:
        raise ValidationException(f"Invalid dose type. Must be one of: {IMMUNIZATION_DOSE_TYPES}")
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(Immunization)
        .where(Immunization.beneficiary_id == b.id)
        .where(Immunization.dose_type == dose_type)
    )
    dose = result.scalar_one_or_none()
    if not dose:
        dose = Immunization(beneficiary_id=b.id, dose_type=dose_type)
        db.add(dose)

    dose.status = payload.status
    dose.received_date = payload.received_date or (date.today() if payload.status == "received" else None)
    await db.commit()

    return success_envelope({
        "message": "Immunization updated",
        "dose_type": dose_type,
        "status": dose.status,
        "date": dose.received_date.isoformat() if dose.received_date else None,
    })


@router.patch("/anc-services/ultrasound/{scan_type}", summary="Update ultrasound scan status")
async def update_ultrasound(
    scan_type: str,
    payload: UltrasoundUpdateRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if scan_type not in ULTRASOUND_SCAN_TYPES:
        raise ValidationException(f"Invalid scan type. Must be one of: {ULTRASOUND_SCAN_TYPES}")
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(UltrasoundScan)
        .where(UltrasoundScan.beneficiary_id == b.id)
        .where(UltrasoundScan.scan_type == scan_type)
    )
    scan = result.scalar_one_or_none()
    if not scan:
        scan = UltrasoundScan(beneficiary_id=b.id, scan_type=scan_type)
        db.add(scan)

    scan.status = payload.status
    scan.scan_date = payload.scan_date or (date.today() if payload.status == "completed" else scan.scan_date)
    if payload.facility_name:
        scan.facility_name = payload.facility_name
    await db.commit()

    return success_envelope({
        "message": "Ultrasound updated",
        "scan_type": scan_type,
        "status": scan.status,
        "scan_date": scan.scan_date.isoformat() if scan.scan_date else None,
    })

@router.patch("/anc-services/visit/{visit_number}/checklist", summary="Update a single ANC visit checklist item (self-reported by the woman)")
async def update_visit_checklist_item(
    visit_number: int,
    payload: ChecklistUpdateRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if visit_number not in ANC_VISIT_TEMPLATE:
        raise ValidationException("Invalid visit number. Must be 1-4")
    valid_keys = {key for key, _, _ in ANC_VISIT_TEMPLATE[visit_number]["items"]}
    if payload.item_key not in valid_keys:
        raise ValidationException(f"Invalid item_key for visit {visit_number}")

    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .where(ANCVisit.visit_number == visit_number)
        .order_by(desc(ANCVisit.visit_date))
    )
    visit = result.scalars().first()
    if not visit:
        # Woman is self-reporting before any formal visit row exists — create one.
        visit = ANCVisit(
            beneficiary_id=b.id,
            visit_number=visit_number,
            visit_date=date.today(),
            checklist={},
        )
        db.add(visit)
        await db.flush()

    checklist = dict(visit.checklist or {})
    checklist[payload.item_key] = payload.checked
    visit.checklist = checklist  # reassignment, not mutation — SQLAlchemy tracks this
    await db.commit()

    template_items = ANC_VISIT_TEMPLATE[visit_number]["items"]
    checked_count = sum(1 for key, _, _ in template_items if checklist.get(key, False))

    return success_envelope({
        "visit_number": visit_number,
        "item_key": payload.item_key,
        "checked": payload.checked,
        "tests_completed": checked_count,
        "tests_total": len(template_items),
        "status": "completed" if checked_count == len(template_items) else "due",
    })

@router.patch("/anc-services/registration", summary="Update a pregnancy registration field (self-reported)")
async def update_registration_field(
    payload: RegistrationFieldUpdateRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    reg = await get_or_create_registration(b.id, db)

    setattr(reg, payload.field, payload.checked)
    if payload.field == "is_registered" and payload.checked and not reg.registered_date:
        reg.registered_date = date.today()
    await db.commit()
    await db.refresh(reg)

    return success_envelope({
        "field": payload.field,
        "checked": payload.checked,
        "is_registered": reg.is_registered,
        "registered_date": reg.registered_date.isoformat() if reg.registered_date else None,
        "rch_id_generated": reg.rch_id_generated,
        "mcp_card_received": reg.mcp_card_received,
    })


@router.get("/anc-services/medicine/{medicine_type}/calendar", summary="Get taken-dates for a medicine (for the calendar view)")
async def get_medicine_calendar(
    medicine_type: str,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if medicine_type not in MEDICINE_DEFAULTS:
        raise ValidationException("Invalid medicine type. Use 'iron' or 'calcium'")
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(MedicineIntakeLog)
        .where(MedicineIntakeLog.beneficiary_id == b.id)
        .where(MedicineIntakeLog.medicine_type == medicine_type)
        .order_by(MedicineIntakeLog.taken_date)
    )
    logs = result.scalars().all()

    return success_envelope({
        "medicine_type": medicine_type,
        "taken_dates": [log.taken_date.isoformat() for log in logs],
    })


@router.patch("/anc-services/medicine/{medicine_type}/date/{taken_date}", summary="Toggle a specific date as taken/untaken")
async def toggle_medicine_date(
    medicine_type: str,
    taken_date: date,
    payload: MedicineDateToggleRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    if medicine_type not in MEDICINE_DEFAULTS:
        raise ValidationException("Invalid medicine type. Use 'iron' or 'calcium'")
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(MedicineIntakeLog)
        .where(MedicineIntakeLog.beneficiary_id == b.id)
        .where(MedicineIntakeLog.medicine_type == medicine_type)
        .where(MedicineIntakeLog.taken_date == taken_date)
    )
    existing_log = result.scalar_one_or_none()

    tracker_result = await db.execute(
        select(MedicineTracker)
        .where(MedicineTracker.beneficiary_id == b.id)
        .where(MedicineTracker.medicine_type == medicine_type)
    )
    tracker = tracker_result.scalar_one_or_none()
    if not tracker:
        tracker = MedicineTracker(beneficiary_id=b.id, medicine_type=medicine_type, total_doses=MEDICINE_DEFAULTS[medicine_type])
        db.add(tracker)
        await db.flush()

    if payload.taken and not existing_log:
        db.add(MedicineIntakeLog(beneficiary_id=b.id, medicine_type=medicine_type, taken_date=taken_date))
        tracker.doses_taken = min(tracker.doses_taken + 1, tracker.total_doses)
        tracker.last_taken_date = max(taken_date, tracker.last_taken_date) if tracker.last_taken_date else taken_date
    elif not payload.taken and existing_log:
        await db.delete(existing_log)
        tracker.doses_taken = max(tracker.doses_taken - 1, 0)

    await db.commit()
    await db.refresh(tracker)

    return success_envelope({
        "medicine_type": medicine_type,
        "date": taken_date.isoformat(),
        "taken": payload.taken,
        "doses_taken": tracker.doses_taken,
        "total_doses": tracker.total_doses,
    })

# ── Appointments ───────────────────────────────────────────────────────────────

@router.get("/appointments", summary="List upcoming appointments")
async def get_appointments(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(Appointment)
        .where(Appointment.beneficiary_id == b.id)
        .where(Appointment.status != "cancelled")
        .order_by(Appointment.scheduled_at)
    )
    appointments = result.scalars().all()
    return success_envelope([AppointmentOut.model_validate(a).model_dump() for a in appointments])


@router.post("/appointments", summary="Book a new appointment")
async def create_appointment(
    payload: AppointmentCreate,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)

    if payload.scheduled_at <= datetime.now(timezone.utc):
        raise ValidationException("Appointment must be scheduled in the future")

    appointment = Appointment(
        beneficiary_id=b.id,
        appointment_type=payload.appointment_type,
        scheduled_at=payload.scheduled_at,
        facility_name=payload.facility_name,
        notes=payload.notes,
    )
    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)

    return success_envelope({
        "message": "Appointment booked",
        "appointment": AppointmentOut.model_validate(appointment).model_dump(),
    })


# ── Reminders ──────────────────────────────────────────────────────────────────

@router.get("/reminders", summary="List pending and upcoming reminders")
async def get_reminders(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)

    result = await db.execute(
        select(Reminder)
        .where(Reminder.beneficiary_id == b.id)
        .where(Reminder.status.in_(["pending", "sent"]))
        .order_by(Reminder.scheduled_at)
    )
    reminders = result.scalars().all()
    return success_envelope([ReminderOut.model_validate(r).model_dump() for r in reminders])


# ── Schemes ────────────────────────────────────────────────────────────────────

SCHEME_CATEGORIES = ["jsy", "jssk", "pmsma", "pmmvy", "minimata"]

@router.get("/schemes", summary="List all maternal schemes")
async def get_schemes(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EducationalContent)
        .where(EducationalContent.category == "scheme")
        .where(EducationalContent.is_active == True)
    )
    schemes = result.scalars().all()
    return success_envelope([{
        "id": str(s.id),
        "title": s.title_hi if lang == "hi" else s.title_en,
        "content": s.content_hi if lang == "hi" else s.content_en,
        "media_url": s.media_url,
        "tags": s.tags,
    } for s in schemes])


@router.get("/schemes/eligibility", summary="Check eligibility for all schemes")
async def get_scheme_eligibility(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    pregnancy = compute_pregnancy_info(b.lmp)

    # Simplified eligibility rules
    return success_envelope({
        "jsy": True,  # All pregnant women
        "jssk": True,  # All pregnant women
        "pmsma": pregnancy["trimester"] >= 2,  # From 2nd trimester
        "pmmvy": True,  # First living child condition (simplified)
        "minimata": b.district in ["Raipur", "Durg", "Bilaspur"],  # State-specific
        "eligibility_reasons": {
            "jsy": "Eligible for institutional delivery cash benefit",
            "jssk": "Eligible for free ANC, delivery and post-natal care",
            "pmsma": "Eligible for PMSMA ANC checkup" if pregnancy["trimester"] >= 2 else "Eligible from 2nd trimester",
            "pmmvy": "Eligible for maternity benefit",
            "minimata": "Check district eligibility",
        }
    })


@router.get("/schemes/{scheme_id}", summary="Get scheme details")
async def get_scheme_detail(
    scheme_id: UUID,
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EducationalContent).where(EducationalContent.id == scheme_id)
    )
    scheme = result.scalar_one_or_none()
    if not scheme:
        raise NotFoundException("Scheme")

    return success_envelope({
        "id": str(scheme.id),
        "title": scheme.title_hi if lang == "hi" else scheme.title_en,
        "content": scheme.content_hi if lang == "hi" else scheme.content_en,
        "media_url": scheme.media_url,
        "tags": scheme.tags,
    })


# ── FAQs ───────────────────────────────────────────────────────────────────────

@router.get("/faqs", summary="Get FAQs (bilingual)")
async def get_faqs(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(FAQ).where(FAQ.is_active == True)
    if category:
        query = query.where(FAQ.category == category)
    query = query.order_by(FAQ.display_order, FAQ.created_at)

    result = await db.execute(query)
    faqs = result.scalars().all()

    return success_envelope([FAQOut.model_validate(f) for f in faqs])


# ── Chatbot ────────────────────────────────────────────────────────────────────

@router.get("/chatbot/history", summary="Get chatbot conversation history")
async def get_chatbot_history(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatbotConversation).where(ChatbotConversation.user_id == user.id)
    )
    conversation = result.scalar_one_or_none()
    messages = conversation.messages if conversation else []
    return success_envelope({"messages": messages, "total": len(messages)})


@router.post("/chatbot/message", summary="Send a message to the AI chatbot")
async def send_chatbot_message(
    payload: ChatbotMessageRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    """
    Calls OpenAI/Gemini with maternal health system prompt.
    Saves conversation history for continuity.
    """
    result = await db.execute(
        select(ChatbotConversation).where(ChatbotConversation.user_id == user.id)
    )
    conversation = result.scalar_one_or_none()
    messages: list = conversation.messages if conversation else []

    # Get AI reply
    reply, sources, suggestions = await get_ai_reply(
        user_message=payload.message,
        language=payload.language,
        history=messages,
    )

    # Append to history
    messages.append({"role": "user", "content": payload.message, "timestamp": datetime.now(timezone.utc).isoformat()})
    messages.append({"role": "assistant", "content": reply, "timestamp": datetime.now(timezone.utc).isoformat()})

    if conversation:
        conversation.messages = messages
        conversation.updated_at = datetime.now(timezone.utc)
    else:
        conversation = ChatbotConversation(user_id=user.id, messages=messages)
        db.add(conversation)

    await db.commit()

    return success_envelope(ChatbotMessageResponse(
        reply=reply,
        language=payload.language,
        sources=sources,
        suggested_questions=suggestions,
    ).model_dump())


@router.post("/chatbot/feedback", summary="Submit feedback on a chatbot response")
async def chatbot_feedback(
    payload: ChatbotFeedbackRequest,
    user: User = Depends(get_current_woman),
):
    # Store feedback for model improvement (Celery task in production)
    return success_envelope({"message": "Feedback recorded. Thank you!"})


@router.delete("/chatbot/history", summary="Clear conversation history")
async def clear_chatbot_history(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatbotConversation).where(ChatbotConversation.user_id == user.id)
    )
    conversation = result.scalar_one_or_none()
    if conversation:
        conversation.messages = []
        await db.commit()
    return success_envelope({"message": "Conversation history cleared"})


# ── Danger Sign / Emergency ────────────────────────────────────────────────────

@router.post("/danger-sign", summary="Report a danger sign → triggers alert to ASHA")
async def report_danger_sign(
    payload: DangerSignRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a danger_sign alert assigned to beneficiary's ASHA.
    Sends FCM push to ASHA immediately.
    """
    b = await get_beneficiary_or_404(user, db)

    # Determine severity: red if any critical symptom
    critical = {"heavy_bleeding", "unconsciousness", "convulsions", "water_broken_early"}
    severity = RiskLevel.red if any(s in critical for s in payload.symptoms) else RiskLevel.yellow

    alert = Alert(
        beneficiary_id=b.id,
        triggered_by=user.id,
        alert_type=AlertType.danger_sign,
        severity=severity,
        symptoms={"symptoms": payload.symptoms, "notes": payload.additional_notes},
        notes=payload.additional_notes,
        assigned_to=b.asha_id,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    # Notify ASHA via FCM
    await send_alert_to_asha(alert=alert, beneficiary=b, db=db)

    return success_envelope({
        "alert_id": str(alert.id),
        "severity": severity.value,
        "message_hi": "आपकी जानकारी मिल गई। आशा कार्यकर्ता को सूचित किया गया है।",
        "message_en": "Alert received. Your ASHA worker has been notified.",
        "asha_notified": b.asha_id is not None,
    })


@router.post("/emergency", summary="Full emergency - alert + notify ASHA + ambulance info")
async def emergency_sos(
    payload: EmergencyRequest,
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    """
    Critical endpoint. Always returns 200 with ambulance numbers.
    Creates RED severity alert and notifies ASHA + admin immediately.
    """
    b = await get_beneficiary_or_404(user, db)

    alert = Alert(
        beneficiary_id=b.id,
        triggered_by=user.id,
        alert_type=AlertType.danger_sign,
        severity=RiskLevel.red,
        symptoms={"symptoms": payload.symptoms, "notes": payload.additional_notes},
        notes=payload.additional_notes,
        assigned_to=b.asha_id,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    # Notify ASHA + admins
    if payload.notify_asha:
        await send_alert_to_asha(alert=alert, beneficiary=b, db=db)

    asha_name = None
    asha_mobile = None
    if b.asha_id and b.asha_worker:
        asha_user = b.asha_worker.user
        asha_name = asha_user.name
        asha_mobile = asha_user.mobile

    return success_envelope(EmergencyResponse(
        alert_id=alert.id,
        alert_severity="red",
        ambulance_number="102",
        alternate_ambulance="108",
        asha_notified=payload.notify_asha and b.asha_id is not None,
        asha_name=asha_name,
        asha_mobile=asha_mobile,
        message_hi="घबराएं नहीं। एम्बुलेंस के लिए 102 या 108 डायल करें। आशा कार्यकर्ता को सूचित कर दिया गया है।",
        message_en="Stay calm. Dial 102 or 108 for ambulance. Your ASHA worker has been notified.",
        nearby_facilities=[],  # Populated by GIS query in production
    ).model_dump())


@router.get("/emergency/contacts", summary="Get emergency contact list")
async def get_emergency_contacts(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    contacts = [
        {"name": "Ambulance (102)", "number": "102", "type": "ambulance"},
        {"name": "Ambulance (108)", "number": "108", "type": "ambulance"},
        {"name": "Women Helpline", "number": "181", "type": "helpline"},
    ]
    if b.asha_id and b.asha_worker:
        contacts.insert(0, {
            "name": f"ASHA - {b.asha_worker.user.name}",
            "number": b.asha_worker.user.mobile,
            "type": "asha",
        })
    return success_envelope(contacts)

# ── Symptom Checker ────────────────────────────────────────────────────────────
 
SYMPTOM_GUIDE = {
    "headache": {
        "hi": "सिरदर्द",
        "en": "Headache",
        "mild": {"hi": "आराम करें, पानी पिएं", "en": "Rest and drink water"},
        "severe": {"hi": "तुरंत डॉक्टर से मिलें — यह प्री-एक्लेम्पसिया हो सकता है", "en": "See doctor immediately — could be pre-eclampsia"},
        "is_danger": True,
    },
    "nausea": {
        "hi": "जी मिचलाना",
        "en": "Nausea / Vomiting",
        "mild": {"hi": "थोड़ा-थोड़ा खाएं, अदरक की चाय पिएं", "en": "Eat small meals, try ginger tea"},
        "severe": {"hi": "उल्टी नहीं रुक रही तो ANM से मिलें", "en": "If vomiting persists, contact ANM"},
        "is_danger": False,
    },
    "bleeding": {
        "hi": "रक्तस्राव",
        "en": "Bleeding",
        "mild": {"hi": "तुरंत ASHA को बुलाएं", "en": "Call ASHA immediately"},
        "severe": {"hi": "तुरंत 102 डायल करें — आपातकाल", "en": "Call 102 immediately — Emergency"},
        "is_danger": True,
    },
    "abdominal_pain": {
        "hi": "पेट दर्द",
        "en": "Abdominal pain",
        "mild": {"hi": "आराम करें, ASHA को सूचित करें", "en": "Rest and inform ASHA"},
        "severe": {"hi": "तुरंत अस्पताल जाएं, 108 डायल करें", "en": "Go to hospital immediately, dial 108"},
        "is_danger": True,
    },
    "swelling": {
        "hi": "सूजन",
        "en": "Swelling (face/hands/feet)",
        "mild": {"hi": "पैर ऊपर रखें, कम नमक खाएं", "en": "Elevate feet, reduce salt intake"},
        "severe": {"hi": "चेहरे/हाथ में सूजन है तो तुरंत डॉक्टर से मिलें", "en": "Facial/hand swelling — see doctor immediately"},
        "is_danger": True,
    },
    "reduced_movement": {
        "hi": "बच्चे की हलचल कम",
        "en": "Reduced fetal movement",
        "mild": {"hi": "ठंडा पानी पिएं, लेट जाएं और गिनें", "en": "Drink cold water, lie down and count movements"},
        "severe": {"hi": "2 घंटे में 10 से कम हलचल — तुरंत अस्पताल जाएं", "en": "Less than 10 movements in 2 hours — go to hospital immediately"},
        "is_danger": True,
    },
    "fever": {
        "hi": "बुखार",
        "en": "Fever",
        "mild": {"hi": "पानी पिएं, आराम करें, पेरासिटामोल लें", "en": "Drink fluids, rest, take paracetamol"},
        "severe": {"hi": "तेज बुखार 101°F से ज्यादा — डॉक्टर से मिलें", "en": "High fever over 101°F — consult doctor"},
        "is_danger": False,
    },
    "back_pain": {
        "hi": "पीठ दर्द",
        "en": "Back pain",
        "mild": {"hi": "सही तरीके से बैठें, हल्की मालिश करें", "en": "Sit properly, gentle massage"},
        "severe": {"hi": "तेज पीठ दर्द — ASHA को सूचित करें", "en": "Severe back pain — inform ASHA"},
        "is_danger": False,
    },
    "difficulty_breathing": {
        "hi": "सांस लेने में तकलीफ",
        "en": "Difficulty breathing",
        "mild": {"hi": "बाईं करवट लेटें", "en": "Lie on your left side"},
        "severe": {"hi": "तुरंत 102/108 डायल करें", "en": "Call 102/108 immediately"},
        "is_danger": True,
    },
}
 
@router.get("/symptom-checker", summary="Symptom checker with guidance by severity",
            operation_id="women_symptom_checker")
async def symptom_checker(
    symptom: Optional[str] = Query(None, description="Symptom code to check"),
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
):
    """
    Returns guidance for each symptom with mild/severe advice.
    If symptom param given, returns specific guidance.
    Otherwise returns full symptom list.
    """
    if symptom and symptom in SYMPTOM_GUIDE:
        s = SYMPTOM_GUIDE[symptom]
        return success_envelope({
            "symptom": symptom,
            "name": s[lang],
            "mild_advice": s["mild"][lang],
            "severe_advice": s["severe"][lang],
            "is_danger_sign": s["is_danger"],
            "emergency_number": "102",
            "asha_action": "संपर्क करें" if lang == "hi" else "Contact ASHA",
        })
 
    # Return all symptoms
    symptoms_list = [
        {
            "code": k,
            "name": v[lang],
            "is_danger_sign": v["is_danger"],
        }
        for k, v in SYMPTOM_GUIDE.items()
    ]
    danger_signs = [s for s in symptoms_list if s["is_danger_sign"]]
    normal_signs = [s for s in symptoms_list if not s["is_danger_sign"]]
 
    return success_envelope({
        "danger_signs": danger_signs,
        "common_symptoms": normal_signs,
        "emergency_note": {
            "hi": "किसी भी खतरे के लक्षण दिखें तो तुरंत 102 या 108 डायल करें",
            "en": "For any danger sign, call 102 or 108 immediately",
        }[lang],
        "total_symptoms": len(symptoms_list),
    })
 
 
# ── Nutrition Counselling ──────────────────────────────────────────────────────
 
NUTRITION_BY_TRIMESTER = {
    1: {
        "title": {"hi": "पहली तिमाही पोषण (1-13 सप्ताह)", "en": "First Trimester Nutrition (Week 1-13)"},
        "key_nutrients": [
            {"nutrient": "Folic Acid", "hi": "फोलिक एसिड", "sources_hi": "हरी पत्तेदार सब्जियां, दाल, अंडे", "sources_en": "Green leafy vegetables, lentils, eggs", "importance_hi": "बच्चे के मस्तिष्क और रीढ़ की हड्डी के विकास के लिए"},
            {"nutrient": "Iron", "hi": "आयरन", "sources_hi": "पालक, चुकंदर, गुड़, दाल", "sources_en": "Spinach, beetroot, jaggery, lentils", "importance_hi": "खून की कमी से बचाता है"},
            {"nutrient": "Vitamin B12", "hi": "विटामिन B12", "sources_hi": "दूध, दही, अंडे, मछली", "sources_en": "Milk, curd, eggs, fish", "importance_hi": "तंत्रिका तंत्र के विकास के लिए"},
        ],
        "foods_to_eat": {"hi": ["दाल", "हरी सब्जियां", "फल", "दूध", "अनाज"], "en": ["Lentils", "Green vegetables", "Fruits", "Milk", "Grains"]},
        "foods_to_avoid": {"hi": ["कच्चा पपीता", "अनानास", "कच्चा मांस", "अत्यधिक चाय/कॉफी"], "en": ["Raw papaya", "Pineapple", "Raw meat", "Excess tea/coffee"]},
        "daily_calories": "2100-2200",
        "water_intake": {"hi": "8-10 गिलास पानी प्रतिदिन", "en": "8-10 glasses of water daily"},
    },
    2: {
        "title": {"hi": "दूसरी तिमाही पोषण (14-27 सप्ताह)", "en": "Second Trimester Nutrition (Week 14-27)"},
        "key_nutrients": [
            {"nutrient": "Calcium", "hi": "कैल्शियम", "sources_hi": "दूध, दही, पनीर, तिल", "sources_en": "Milk, curd, paneer, sesame", "importance_hi": "बच्चे की हड्डियों और दांतों के लिए"},
            {"nutrient": "Protein", "hi": "प्रोटीन", "sources_hi": "दाल, सोयाबीन, अंडे, मछली", "sources_en": "Lentils, soybean, eggs, fish", "importance_hi": "बच्चे की मांसपेशियों के विकास के लिए"},
            {"nutrient": "Omega-3", "hi": "ओमेगा-3", "sources_hi": "अखरोट, मछली, अलसी", "sources_en": "Walnuts, fish, flaxseed", "importance_hi": "बच्चे के मस्तिष्क के विकास के लिए"},
        ],
        "foods_to_eat": {"hi": ["प्रोटीन युक्त दाल", "कैल्शियम युक्त डेयरी", "ओमेगा-3 युक्त मेवे", "आयरन युक्त हरी सब्जियां"], "en": ["Protein-rich lentils", "Calcium-rich dairy", "Omega-3 nuts", "Iron-rich greens"]},
        "foods_to_avoid": {"hi": ["तला हुआ खाना", "जंक फूड", "अत्यधिक नमक", "शराब"], "en": ["Fried food", "Junk food", "Excess salt", "Alcohol"]},
        "daily_calories": "2200-2400",
        "water_intake": {"hi": "10 गिलास पानी प्रतिदिन", "en": "10 glasses of water daily"},
    },
    3: {
        "title": {"hi": "तीसरी तिमाही पोषण (28-40 सप्ताह)", "en": "Third Trimester Nutrition (Week 28-40)"},
        "key_nutrients": [
            {"nutrient": "Iron", "hi": "आयरन", "sources_hi": "पालक, चुकंदर, गुड़, किशमिश", "sources_en": "Spinach, beetroot, jaggery, raisins", "importance_hi": "प्रसव के दौरान खून की कमी से बचाने के लिए"},
            {"nutrient": "Vitamin C", "hi": "विटामिन C", "sources_hi": "आंवला, संतरा, नींबू, टमाटर", "sources_en": "Amla, orange, lemon, tomato", "importance_hi": "आयरन के अवशोषण में मदद करता है"},
            {"nutrient": "Magnesium", "hi": "मैग्नीशियम", "sources_hi": "केला, बादाम, हरी सब्जियां", "sources_en": "Banana, almonds, green vegetables", "importance_hi": "मांसपेशियों में ऐंठन से राहत"},
        ],
        "foods_to_eat": {"hi": ["आयरन युक्त खाना", "विटामिन C", "हल्का सुपाच्य भोजन", "घर का बना खाना"], "en": ["Iron-rich foods", "Vitamin C foods", "Light digestible meals", "Home-cooked food"]},
        "foods_to_avoid": {"hi": ["भारी तला खाना", "बहुत मसालेदार", "कच्चा खाना", "बाहर का खाना"], "en": ["Heavy fried food", "Very spicy food", "Raw food", "Outside food"]},
        "daily_calories": "2400-2500",
        "water_intake": {"hi": "10-12 गिलास पानी प्रतिदिन", "en": "10-12 glasses of water daily"},
    },
}
 
@router.get("/nutrition", summary="Nutrition counselling by trimester",
            operation_id="women_nutrition")
async def get_nutrition(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    trimester = get_trimester(b.lmp)
    week = get_week(b.lmp)
    data = NUTRITION_BY_TRIMESTER[trimester]
 
    return success_envelope({
        "trimester": trimester,
        "week": week,
        "title": data["title"][lang],
        "key_nutrients": [{
            "nutrient": n["nutrient"],
            "name": n["hi"] if lang == "hi" else n["nutrient"],
            "food_sources": n["sources_hi"] if lang == "hi" else n["sources_en"],
            "importance": n["importance_hi"] if lang == "hi" else n["importance_hi"],
        } for n in data["key_nutrients"]],
        "foods_to_eat": data["foods_to_eat"][lang],
        "foods_to_avoid": data["foods_to_avoid"][lang],
        "daily_calories": data["daily_calories"],
        "water_intake": data["water_intake"][lang],
        "tip": {
            "hi": "थोड़ा-थोड़ा 5-6 बार खाएं। एक बार में ज्यादा न खाएं।",
            "en": "Eat small meals 5-6 times a day. Avoid eating too much at once.",
        }[lang],
    })
 
 
# ── Lifestyle & Activity Guidance ──────────────────────────────────────────────
 
@router.get("/lifestyle", summary="Lifestyle and activity guidance by trimester",
            operation_id="women_lifestyle")
async def get_lifestyle(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    trimester = get_trimester(b.lmp)
    week = get_week(b.lmp)
 
    guidance = {
        1: {
            "exercise": {
                "hi": ["हल्की सैर 15-20 मिनट", "योग — प्राणायाम और हल्के आसन", "तैराकी (अगर आदत हो)"],
                "en": ["Light walk 15-20 minutes", "Yoga — pranayama and gentle poses", "Swimming (if accustomed)"],
            },
            "avoid": {
                "hi": ["भारी वजन उठाना", "पेट पर दबाव डालने वाले व्यायाम", "अत्यधिक थकान"],
                "en": ["Heavy lifting", "Abdominal pressure exercises", "Excessive exertion"],
            },
            "sleep": {"hi": "8-9 घंटे की नींद लें", "en": "Get 8-9 hours of sleep"},
            "posture": {"hi": "सीधे बैठें, झुककर न बैठें", "en": "Sit straight, avoid slouching"},
            "work": {"hi": "हल्का काम कर सकती हैं, भारी काम न करें", "en": "Light work is fine, avoid heavy work"},
        },
        2: {
            "exercise": {
                "hi": ["30 मिनट सैर", "गर्भावस्था योग", "हल्की स्ट्रेचिंग", "कीगल व्यायाम"],
                "en": ["30-minute walk", "Prenatal yoga", "Light stretching", "Kegel exercises"],
            },
            "avoid": {
                "hi": ["पीठ के बल लेटकर व्यायाम", "कूदना या दौड़ना", "गर्म स्नान"],
                "en": ["Lying flat on back exercises", "Jumping or running", "Hot baths"],
            },
            "sleep": {"hi": "बाईं करवट सोएं — यह बच्चे के लिए सबसे अच्छा है", "en": "Sleep on left side — best for baby"},
            "posture": {"hi": "उठने-बैठने में सावधानी रखें", "en": "Be careful when getting up or sitting down"},
            "work": {"hi": "मध्यम काम कर सकती हैं", "en": "Moderate work is fine"},
        },
        3: {
            "exercise": {
                "hi": ["धीमी सैर 20-30 मिनट", "गहरी सांस के व्यायाम", "कीगल व्यायाम", "हल्की स्ट्रेचिंग"],
                "en": ["Slow walk 20-30 minutes", "Deep breathing exercises", "Kegel exercises", "Light stretching"],
            },
            "avoid": {
                "hi": ["लंबे समय तक खड़े रहना", "सीढ़ियां चढ़ना-उतरना", "अकेले यात्रा"],
                "en": ["Standing for long periods", "Climbing stairs repeatedly", "Traveling alone"],
            },
            "sleep": {"hi": "बाईं करवट सोएं, तकिया पैरों के बीच रखें", "en": "Sleep on left side with pillow between legs"},
            "posture": {"hi": "धीरे-धीरे उठें, झटके से न उठें", "en": "Rise slowly, avoid sudden movements"},
            "work": {"hi": "हल्का काम करें, ज्यादा थकान से बचें", "en": "Light work only, avoid exhaustion"},
        },
    }
 
    g = guidance[trimester]
    return success_envelope({
        "trimester": trimester,
        "week": week,
        "exercise": {
            "recommended": g["exercise"][lang],
            "avoid": g["avoid"][lang],
        },
        "sleep": g["sleep"][lang],
        "posture": g["posture"][lang],
        "work": g["work"][lang],
        "warning": {
            "hi": "कोई भी नया व्यायाम शुरू करने से पहले अपनी ANM से सलाह लें",
            "en": "Consult your ANM before starting any new exercise",
        }[lang],
    })
 
 
# ── Test & Investigation Tracker ───────────────────────────────────────────────
 
RECOMMENDED_TESTS = {
    1: [
        {"name": "Blood Group & Rh Factor", "hi": "रक्त समूह और Rh फैक्टर", "when": "Week 8-12", "mandatory": True},
        {"name": "Hemoglobin", "hi": "हीमोग्लोबिन", "when": "Week 8-12", "mandatory": True},
        {"name": "Blood Sugar (Fasting)", "hi": "ब्लड शुगर (फास्टिंग)", "when": "Week 10", "mandatory": True},
        {"name": "HIV Test", "hi": "HIV जांच", "when": "Week 8-12", "mandatory": True},
        {"name": "Urine Test", "hi": "मूत्र जांच", "when": "Every ANC visit", "mandatory": True},
        {"name": "Ultrasound (Dating Scan)", "hi": "अल्ट्रासाउंड (डेटिंग स्कैन)", "when": "Week 11-13", "mandatory": True},
    ],
    2: [
        {"name": "Hemoglobin", "hi": "हीमोग्लोबिन", "when": "Week 20-24", "mandatory": True},
        {"name": "Blood Pressure", "hi": "ब्लड प्रेशर", "when": "Every ANC visit", "mandatory": True},
        {"name": "Anomaly Scan", "hi": "एनोमली स्कैन", "when": "Week 18-20", "mandatory": True},
        {"name": "Glucose Challenge Test", "hi": "ग्लूकोज चैलेंज टेस्ट", "when": "Week 24-28", "mandatory": False},
        {"name": "Urine Culture", "hi": "मूत्र कल्चर", "when": "Week 20", "mandatory": False},
    ],
    3: [
        {"name": "Hemoglobin", "hi": "हीमोग्लोबिन", "when": "Week 32-36", "mandatory": True},
        {"name": "Blood Pressure", "hi": "ब्लड प्रेशर", "when": "Every ANC visit", "mandatory": True},
        {"name": "Growth Scan", "hi": "ग्रोथ स्कैन", "when": "Week 32-34", "mandatory": True},
        {"name": "Non-Stress Test (NST)", "hi": "NST टेस्ट", "when": "Week 36+", "mandatory": False},
        {"name": "CBC (Complete Blood Count)", "hi": "पूर्ण रक्त गणना", "when": "Week 36", "mandatory": True},
    ],
}
 
@router.get("/tests", summary="Test and investigation tracker by trimester",
            operation_id="women_tests_get")
async def get_tests(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    trimester = get_trimester(b.lmp)
    week = get_week(b.lmp)
 
    # Get ANC visits to check what's already been done
    anc_result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .order_by(desc(ANCVisit.visit_date))
    )
    visits = anc_result.scalars().all()
    total_visits = len(visits)
 
    recommended = RECOMMENDED_TESTS.get(trimester, [])
 
    return success_envelope({
        "trimester": trimester,
        "week": week,
        "total_anc_visits_done": total_visits,
        "recommended_tests": [{
            "name": t["name"],
            "display_name": t["hi"] if lang == "hi" else t["name"],
            "when": t["when"],
            "is_mandatory": t["mandatory"],
        } for t in recommended],
        "mandatory_count": sum(1 for t in recommended if t["mandatory"]),
        "note": {
            "hi": "सभी जरूरी जांच नजदीकी PHC या सरकारी अस्पताल में मुफ्त में होती हैं (JSSK योजना)",
            "en": "All mandatory tests are free at nearest PHC or government hospital (JSSK scheme)",
        }[lang],
        "next_anc_due": visits[0].next_due_date.isoformat() if visits and visits[0].next_due_date else None,
    })
 
 
# ── Personal Health Dashboard ──────────────────────────────────────────────────
 
@router.get("/health-dashboard", summary="Personal health dashboard — all vitals summary",
            operation_id="women_health_dashboard")
async def get_health_dashboard(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    """
    Single endpoint that powers the personal health dashboard screen.
    Returns all key health metrics, trends, and alerts in one call.
    """
    b = await get_beneficiary_or_404(user, db)
    today = date.today()
    lmp = b.lmp
    weeks = min((today - lmp).days // 7, 42)
    trimester = 1 if weeks < 14 else (2 if weeks < 28 else 3)
    edd = lmp + timedelta(days=280)
    days_left = max(0, (edd - today).days)
 
    # ANC visits
    anc_result = await db.execute(
        select(ANCVisit)
        .where(ANCVisit.beneficiary_id == b.id)
        .order_by(desc(ANCVisit.visit_date))
    )
    visits = anc_result.scalars().all()
    last_visit = visits[0] if visits else None
 
    # BPCR score
    bpcr_result = await db.execute(
        select(BPCRAssessment)
        .where(BPCRAssessment.beneficiary_id == b.id)
        .order_by(desc(BPCRAssessment.assessed_at))
    )
    assessments = bpcr_result.scalars().all()
    seen = {}
    for a in assessments:
        if a.component not in seen:
            seen[a.component] = a
    bpcr_score = sum(a.score or 0 for a in seen.values()) if seen else None
 
    # Alerts
    alert_result = await db.execute(
        select(Alert)
        .where(Alert.beneficiary_id == b.id)
        .where(Alert.status.in_(["pending", "contacted"]))
    )
    active_alerts = alert_result.scalars().all()
 
    # Health flags
    flags = []
    if last_visit:
        if last_visit.hemoglobin and float(last_visit.hemoglobin) < 11:
            flags.append({"type": "anemia", "hi": "खून की कमी", "en": "Anemia", "severity": "warning"})
        if last_visit.bp_systolic and last_visit.bp_systolic > 140:
            flags.append({"type": "high_bp", "hi": "उच्च रक्तचाप", "en": "High BP", "severity": "danger"})
 
    next_due = last_visit.next_due_date if last_visit else None
    is_overdue = next_due is not None and next_due < today
 
    return success_envelope({
        "pregnancy": {
            "weeks": weeks,
            "trimester": trimester,
            "days_until_edd": days_left,
            "edd": edd.isoformat(),
            "risk_level": b.risk_level.value if b.risk_level else "low",
        },
        "vitals": {
            "last_weight_kg": last_visit.weight_kg if last_visit else None,
            "last_bp": f"{last_visit.bp_systolic}/{last_visit.bp_diastolic}" if last_visit and last_visit.bp_systolic else None,
            "last_hemoglobin": last_visit.hemoglobin if last_visit else None,
            "last_fhr": last_visit.fetal_heart_rate if last_visit else None,
            "last_visit_date": last_visit.visit_date.isoformat() if last_visit else None,
        },
        "anc": {
            "total_visits": len(visits),
            "recommended_visits": 4,
            "next_due": next_due.isoformat() if next_due else None,
            "is_overdue": is_overdue,
            "overdue_days": (today - next_due).days if is_overdue else None,
        },
        "bpcr": {
            "score": bpcr_score,
            "max_score": 10,
            "completed_components": len(seen),
            "risk": "Green" if bpcr_score and bpcr_score >= 8 else ("Yellow" if bpcr_score and bpcr_score >= 5 else "Red") if bpcr_score is not None else "Not assessed",
        },
        "health_flags": flags,
        "active_alerts": len(active_alerts),
        "reminders": {
            "anc_reminder": f"ANC visit due on {next_due.isoformat()}" if next_due else None,
            "bpcr_pending": 10 - len(seen) if len(seen) < 10 else 0,
        },
    })
 
 
# ── Postnatal Care ─────────────────────────────────────────────────────────────
 
@router.get("/postnatal", summary="Postnatal care guide",
            operation_id="women_postnatal")
async def get_postnatal(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
):
    content = {
        "hi": {
            "title": "प्रसव के बाद देखभाल",
            "first_24_hours": [
                "बच्चे को एक घंटे के अंदर स्तनपान कराएं",
                "कंगारू मदर केयर (त्वचा से त्वचा का संपर्क)",
                "रक्तस्राव पर नजर रखें",
                "पेशाब की जांच करें",
            ],
            "first_week": [
                "हर 2-3 घंटे में स्तनपान कराएं",
                "टांकों की सफाई रखें",
                "आराम करें, भारी काम न करें",
                "पौष्टिक खाना खाएं",
            ],
            "danger_signs": [
                "अत्यधिक रक्तस्राव",
                "तेज बुखार 101°F से ज्यादा",
                "स्तन में दर्द या सूजन",
                "अवसाद के लक्षण",
                "पेशाब में जलन",
            ],
            "pnc_visits": "प्रसव के 48 घंटे, 7 दिन और 42 दिन बाद PNC जांच जरूरी है",
        },
        "en": {
            "title": "Postnatal Care Guide",
            "first_24_hours": [
                "Breastfeed within one hour of birth",
                "Kangaroo Mother Care (skin-to-skin contact)",
                "Monitor bleeding",
                "Check urination",
            ],
            "first_week": [
                "Breastfeed every 2-3 hours",
                "Keep stitches clean",
                "Rest, avoid heavy work",
                "Eat nutritious food",
            ],
            "danger_signs": [
                "Excessive bleeding",
                "High fever over 101°F",
                "Breast pain or swelling",
                "Signs of depression",
                "Burning urination",
            ],
            "pnc_visits": "PNC check at 48 hours, 7 days and 42 days after delivery is mandatory",
        },
    }
    return success_envelope(content[lang])
 
 
# ── Newborn Care ───────────────────────────────────────────────────────────────
 
@router.get("/newborn-care", summary="Newborn care guide",
            operation_id="women_newborn_care")
async def get_newborn_care(
    lang: str = Query("hi", pattern=r"^(hi|en)$"),
    user: User = Depends(get_current_woman),
):
    content = {
        "hi": {
            "title": "नवजात शिशु की देखभाल",
            "feeding": [
                "जन्म के एक घंटे के अंदर स्तनपान शुरू करें",
                "पहले 6 महीने केवल स्तनपान कराएं",
                "हर 2-3 घंटे में स्तनपान कराएं",
                "कोलोस्ट्रम (पहला दूध) जरूर पिलाएं — यह बहुत पौष्टिक है",
            ],
            "warmth": [
                "बच्चे को गर्म रखें",
                "जन्म के तुरंत बाद न नहलाएं",
                "कंगारू मदर केयर अपनाएं",
            ],
            "hygiene": [
                "नाभि को साफ और सूखा रखें",
                "हाथ धोकर बच्चे को पकड़ें",
                "डायपर समय पर बदलें",
            ],
            "immunization": [
                "जन्म पर — BCG, OPV-0, Hep-B",
                "6 सप्ताह — DPT, OPV, Hib, Rotavirus",
                "10 सप्ताह — दूसरी खुराक",
                "14 सप्ताह — तीसरी खुराक",
            ],
            "danger_signs": [
                "सांस लेने में तकलीफ",
                "दूध न पीना",
                "पीलिया (पीली त्वचा)",
                "तेज बुखार",
                "दौरे पड़ना",
            ],
        },
        "en": {
            "title": "Newborn Care Guide",
            "feeding": [
                "Start breastfeeding within one hour of birth",
                "Exclusively breastfeed for first 6 months",
                "Feed every 2-3 hours",
                "Give colostrum (first milk) — it is highly nutritious",
            ],
            "warmth": [
                "Keep baby warm",
                "Do not bathe immediately after birth",
                "Practice Kangaroo Mother Care",
            ],
            "hygiene": [
                "Keep cord clean and dry",
                "Wash hands before handling baby",
                "Change diapers regularly",
            ],
            "immunization": [
                "At birth — BCG, OPV-0, Hep-B",
                "6 weeks — DPT, OPV, Hib, Rotavirus",
                "10 weeks — Second dose",
                "14 weeks — Third dose",
            ],
            "danger_signs": [
                "Difficulty breathing",
                "Not feeding",
                "Jaundice (yellow skin)",
                "High fever",
                "Convulsions",
            ],
        },
    }
    return success_envelope(content[lang])
 
 
# ── Medicine Reminders ─────────────────────────────────────────────────────────
 
@router.get("/reminders/medicine", summary="Get medicine reminders",
            operation_id="women_medicine_reminders_get")
async def get_medicine_reminders(
    user: User = Depends(get_current_woman),
    db: AsyncSession = Depends(get_db),
):
    b = await get_beneficiary_or_404(user, db)
    trimester = get_trimester(b.lmp)
 
    # Standard medicines by trimester
    standard_medicines = {
        1: [
            {"name": "Iron & Folic Acid (IFA)", "hi": "आयरन और फोलिक एसिड", "dose": "1 tablet daily", "time": "After dinner", "duration": "Throughout pregnancy"},
            {"name": "Folic Acid 5mg", "hi": "फोलिक एसिड 5mg", "dose": "1 tablet daily", "time": "Morning", "duration": "First 12 weeks"},
        ],
        2: [
            {"name": "Iron & Folic Acid (IFA)", "hi": "आयरन और फोलिक एसिड", "dose": "1 tablet daily", "time": "After dinner", "duration": "Throughout pregnancy"},
            {"name": "Calcium", "hi": "कैल्शियम", "dose": "1 tablet twice daily", "time": "After meals", "duration": "From 20 weeks"},
        ],
        3: [
            {"name": "Iron & Folic Acid (IFA)", "hi": "आयरन और फोलिक एसिड", "dose": "1 tablet daily", "time": "After dinner", "duration": "Throughout pregnancy"},
            {"name": "Calcium", "hi": "कैल्शियम", "dose": "1 tablet twice daily", "time": "After meals", "duration": "Continue"},
        ],
    }
 
    # Get scheduled medicine reminders from DB
    result = await db.execute(
        select(Reminder)
        .where(Reminder.beneficiary_id == b.id)
        .where(Reminder.reminder_type == "medicine")
        .where(Reminder.status.in_(["pending", "sent"]))
        .order_by(Reminder.scheduled_at)
    )
    db_reminders = result.scalars().all()
 
    return success_envelope({
        "standard_medicines": standard_medicines.get(trimester, []),
        "scheduled_reminders": [{
            "id": str(r.id),
            "message_hi": r.message_hi,
            "message_en": r.message_en,
            "scheduled_at": r.scheduled_at.isoformat(),
            "status": r.status.value if r.status else "pending",
        } for r in db_reminders],
        "note": {
            "hi": "सभी दवाइयां ASHA या ANM से मुफ्त में मिलती हैं",
            "en": "All medicines are available free from ASHA or ANM",
        },
    })