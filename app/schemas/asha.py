from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── ASHA Registration & Profile ───────────────────────────────────────────────

class ASHARegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    worker_role: str = Field(..., pattern=r"^(asha|anm)$")
    unique_id: str = Field(..., description="Government issued worker ID")
    subcentre: Optional[str] = None
    village: Optional[str] = None
    block: str = Field(..., description="Block name")
    district: str = Field(..., description="District name")
    preferred_language: str = Field("hi", pattern=r"^(hi|en)$")
    latitude: Optional[str] = None
    longitude: Optional[str] = None


class ASHAProfileUpdate(BaseModel):
    name: Optional[str] = None
    subcentre: Optional[str] = None
    village: Optional[str] = None
    preferred_language: Optional[str] = Field(None, pattern=r"^(hi|en)$")
    fcm_token: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None


class ASHAProfileOut(BaseModel):
    id: UUID
    name: Optional[str]
    mobile: str
    worker_role: str
    unique_id: Optional[str]
    subcentre: Optional[str]
    village: Optional[str]
    block: str
    district: str
    preferred_language: str
    total_beneficiaries: int = 0
    pending_alerts: int = 0


# ── Beneficiary management ────────────────────────────────────────────────────

class BeneficiaryListItem(BaseModel):
    id: UUID
    name: str
    age: Optional[int]
    village: Optional[str]
    lmp: date
    gestational_week: int
    trimester: int
    risk_level: str
    last_anc_date: Optional[date]
    next_anc_due: Optional[date]
    is_anc_overdue: bool
    bpcr_score: Optional[int]
    mobile: Optional[str]
    pending_alerts: int = 0


class BeneficiaryDetailOut(BaseModel):
    id: UUID
    name: str
    age: Optional[int]
    husband_name: Optional[str]
    mobile: Optional[str]
    village: Optional[str]
    block: Optional[str]
    district: Optional[str]
    address: Optional[str]
    lmp: date
    edd: Optional[date]
    blood_group: Optional[str]
    risk_level: str
    gestational_week: int
    trimester: int
    days_until_edd: int
    bpcr_score: Optional[int]
    bpcr_risk: Optional[str]
    total_anc_visits: int
    last_anc_date: Optional[date]
    next_anc_due: Optional[date]
    is_anc_overdue: bool
    high_risk_flags: list[str]
    created_at: Optional[datetime]


# ── ANC Visit Recording ───────────────────────────────────────────────────────

class ANCVisitCreate(BaseModel):
    beneficiary_id: UUID
    visit_date: date
    visit_number: Optional[int] = None
    weight_kg: Optional[str] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    hemoglobin: Optional[str] = None
    fundal_height: Optional[str] = None
    fetal_heart_rate: Optional[int] = None
    notes: Optional[str] = None
    next_due_date: Optional[date] = None


class ANCVisitOut(BaseModel):
    id: UUID
    beneficiary_id: UUID
    beneficiary_name: Optional[str] = None
    visit_date: date
    visit_number: Optional[int]
    weight_kg: Optional[str]
    bp_systolic: Optional[int]
    bp_diastolic: Optional[int]
    hemoglobin: Optional[str]
    fundal_height: Optional[str]
    fetal_heart_rate: Optional[int]
    notes: Optional[str]
    next_due_date: Optional[date]
    high_risk_flags: list[str] = []
    created_at: datetime


# ── BPCR Assessment by ASHA ───────────────────────────────────────────────────

class ASHABPCRRequest(BaseModel):
    beneficiary_id: UUID
    responses: list[dict] = Field(..., description="List of {component, score, response}")


# ── Alerts ────────────────────────────────────────────────────────────────────

class AlertListItem(BaseModel):
    id: UUID
    beneficiary_id: UUID
    beneficiary_name: str
    beneficiary_village: Optional[str]
    beneficiary_mobile: Optional[str]
    alert_type: str
    severity: str
    symptoms: Optional[dict]
    status: str
    triggered_at: datetime
    responded_at: Optional[datetime]
    response_minutes: Optional[int]
    is_overdue: bool


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(contacted|referred|resolved|closed)$")
    notes: Optional[str] = None


class AlertDetailOut(BaseModel):
    id: UUID
    beneficiary_id: UUID
    beneficiary_name: str
    beneficiary_village: Optional[str]
    beneficiary_mobile: Optional[str]
    beneficiary_address: Optional[str]
    alert_type: str
    severity: str
    symptoms: Optional[dict]
    notes: Optional[str]
    status: str
    triggered_at: datetime
    responded_at: Optional[datetime]
    closed_at: Optional[datetime]
    response_minutes: Optional[int]
    is_overdue: bool


# ── Home Dashboard ────────────────────────────────────────────────────────────

class ASHADashboardOut(BaseModel):
    worker_name: str
    worker_role: str
    block: str
    total_beneficiaries: int
    pending_alerts: int
    red_alerts: int
    anc_overdue: int
    low_bpcr: int
    recent_alerts: list[AlertListItem]
    upcoming_anc: list[dict]

# ── Facility ──────────────────────────────────────────────────────────────────
class FacilityOut(BaseModel):
    id: str
    name: str
    type: str
    block: str
    district: str
    latitude: float
    longitude: float
    phone: Optional[str]
    beds: int
    has_labour_room: bool
    has_blood_bank: bool
    distance_km: Optional[float] = None
    estimated_time_min: Optional[int] = None

# ── Visit Log ─────────────────────────────────────────────────────────────────
class VisitLogCreate(BaseModel):
    beneficiary_id: UUID
    visit_type: str = Field(..., pattern=r"^(home_visit|anc_counselling|bpcr_counselling|medicine_distribution|follow_up|emergency_response|other)$")
    notes: Optional[str] = None
    status: str = Field("completed", pattern=r"^(completed|pending|cancelled)$")

class VisitLogOut(BaseModel):
    id: UUID
    visit_type: str
    notes: Optional[str]
    status: str
    visit_date: Optional[str]
    logged_at: Optional[str]

# ── Accountability ────────────────────────────────────────────────────────────
class AccountabilityResponse(BaseModel):
    summary: dict
    action_breakdown: list[dict]
    response_trend: list[dict]
    recent_closed: list[dict]
    period_days: int