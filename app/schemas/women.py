from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Shared ────────────────────────────────────────────────────────────────────

class APIResponse(BaseModel):
    success: bool
    data: Any = None
    meta: Optional[dict] = None
    error: Optional[dict] = None


# ── Auth ──────────────────────────────────────────────────────────────────────

class SendOTPRequest(BaseModel):
    mobile: str = Field(..., min_length=10, max_length=15)

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not re.match(r"^(\+91|91)?[6-9]\d{9}$", v):
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        # Normalize to 10 digits
        if v.startswith("+91"):
            v = v[3:]
        elif v.startswith("91") and len(v) == 12:
            v = v[2:]
        return v


class VerifyOTPRequest(BaseModel):
    mobile: str
    otp: str = Field(..., min_length=6, max_length=6)

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        v = v.strip().replace(" ", "")
        if v.startswith("+91"):
            v = v[3:]
        elif v.startswith("91") and len(v) == 12:
            v = v[2:]
        return v


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AdminLoginRequest(BaseModel):
    email: str
    password: str


# ── User / Profile ─────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: UUID
    mobile: str
    role: str
    name: Optional[str]
    preferred_language: str

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user: UserOut
    tokens: TokenResponse
    is_new_user: bool = False


# ── Women Registration ─────────────────────────────────────────────────────────

class WomenRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    age: int = Field(..., ge=14, le=55)
    husband_name: Optional[str] = None
    husband_age: Optional[int] = None
    dob: Optional[date] = None
    address: Optional[str] = None
    village: Optional[str] = None
    phc: Optional[str] = None
    block: Optional[str] = None
    district: Optional[str] = None
    lmp: date = Field(..., description="Last Menstrual Period date (YYYY-MM-DD)")
    blood_group: Optional[str] = Field(None, pattern=r"^(A|B|AB|O)[+-]$")
    preferred_language: str = Field("hi", pattern=r"^(hi|en)$")
    consent: bool = Field(..., description="Must be True to register")
    latitude: Optional[str] = None
    longitude: Optional[str] = None

    @field_validator("consent")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Consent is required to register")
        return v

    @field_validator("lmp")
    @classmethod
    def lmp_not_future(cls, v: date) -> date:
        from datetime import date as date_cls
        if v > date_cls.today():
            raise ValueError("LMP cannot be a future date")
        return v


class WomenProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    husband_name: Optional[str] = None
    address: Optional[str] = None
    village: Optional[str] = None
    blood_group: Optional[str] = Field(None, pattern=r"^(A|B|AB|O)[+-]$")
    preferred_language: Optional[str] = Field(None, pattern=r"^(hi|en)$")
    fcm_token: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None


class PregnancyInfo(BaseModel):
    lmp: date
    edd: date
    gestational_week: int
    trimester: int
    days_until_edd: int
    risk_level: str


class BeneficiaryOut(BaseModel):
    id: UUID
    name: str
    age: Optional[int] = None
    husband_name: Optional[str] = None
    village: Optional[str] = None
    block: Optional[str] = None
    district: Optional[str] = None
    lmp: date
    edd: Optional[date] = None
    blood_group: Optional[str] = None
    risk_level: str = "low"
    preferred_language: str = "hi"
    asha_name: Optional[str] = None
    anm_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── BPCR ──────────────────────────────────────────────────────────────────────

BPCR_COMPONENTS = [
    "birth_place",
    "skilled_birth_attendant",
    "transport",
    "emergency_funds",
    "blood_donor",
    "decision_maker",
    "support_person",
    "danger_sign_knowledge",
    "newborn_care",
    "postpartum_care",
]


class BPCRComponentResponse(BaseModel):
    component: str = Field(..., description="One of the 10 BPCR components")
    score: int = Field(..., ge=0, le=1)
    response: Optional[dict] = None

    @field_validator("component")
    @classmethod
    def valid_component(cls, v: str) -> str:
        if v not in BPCR_COMPONENTS:
            raise ValueError(f"Invalid component. Must be one of: {BPCR_COMPONENTS}")
        return v


class BPCRRespondRequest(BaseModel):
    responses: list[BPCRComponentResponse] = Field(..., min_length=1, max_length=10)


class BPCRSummary(BaseModel):
    total_score: int
    max_score: int = 10
    percentage: float
    risk_label: str          # "Red" | "Yellow" | "Green"
    completed_components: list[str]
    missing_components: list[str]
    last_assessed_at: Optional[datetime]
    components: list[dict]   # [{component, score, response, assessed_at}]


# ── ANC Services ──────────────────────────────────────────────────────────────

class ANCVisitOut(BaseModel):
    id: UUID
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
    created_at: datetime

    class Config:
        from_attributes = True


class ANCServicesResponse(BaseModel):
    visits: list[ANCVisitOut]
    total_visits: int
    last_visit: Optional[ANCVisitOut]
    next_due_date: Optional[date]
    is_overdue: bool
    overdue_days: Optional[int]
    week_guide: Optional[dict]        # Week-specific information
    high_risk_flags: list[str]        # E.g., ["low_hemoglobin", "high_bp"]


class WeekGuide(BaseModel):
    week: int
    trimester: int
    baby_development: str
    mother_changes: str
    nutrition_tips: list[str]
    warning_signs: list[str]
    what_to_expect: str
    image_url: Optional[str] = None

# ── ANC Visit Checklist Template ──────────────────────────────────────────────
# (key, label_hi, label_en) tuples per visit_number. Drives both the API response
# and what the ASHA app writes into ANCVisit.checklist (JSONB).

ANC_VISIT_TEMPLATE: dict[int, dict] = {
    1: {
        "title_hi": "पहली एएनसी विजिट", "title_en": "First ANC Visit",
        "week_range": "Before 12 Weeks",
        "items": [
            ("weight_recorded", "वजन दर्ज किया गया", "Weight recorded"),
            ("bp_checked", "रक्तचाप जांचा गया", "Blood pressure checked"),
            ("hemoglobin_tested", "हीमोग्लोबिन जांच", "Hemoglobin tested"),
            ("blood_group_tested", "ब्लड ग्रुप जांच", "Blood group tested"),
            ("urine_test_done", "यूरिन टेस्ट", "Urine test done"),
            ("hiv_screening", "एचआईवी जांच", "HIV screening"),
            ("hepatitis_b_screening", "हेपेटाइटिस बी जांच", "Hepatitis B screening"),
            ("counselling_completed", "परामर्श पूर्ण", "Counselling completed"),
        ],
    },
    2: {
        "title_hi": "दूसरी एएनसी विजिट", "title_en": "Second ANC Visit",
        "week_range": "14-26 Weeks",
        "items": [
            ("weight_measured", "वजन मापा गया", "Weight measured"),
            ("bp_checked", "बीपी जांची गई", "BP checked"),
            ("fetal_growth_assessed", "भ्रूण वृद्धि आकलन", "Fetal growth assessed"),
            ("ifa_started", "आईएफए शुरू", "IFA started"),
            ("calcium_started", "कैल्शियम शुरू", "Calcium started"),
            ("danger_sign_counselling", "खतरे के संकेत परामर्श", "Danger sign counselling"),
        ],
    },
    3: {
        "title_hi": "तीसरी एएनसी विजिट", "title_en": "Third ANC Visit",
        "week_range": "28-34 Weeks",
        "items": [
            ("weight_measured", "वजन मापा गया", "Weight measured"),
            ("bp_checked", "बीपी जांची गई", "BP checked"),
            ("fetal_movement_assessment", "भ्रूण गति आकलन", "Fetal movement assessment"),
            ("birth_preparedness_discussed", "प्रसव तैयारी चर्चा", "Birth preparedness discussed"),
            ("high_risk_screening", "उच्च जोखिम जांच", "High-risk screening"),
        ],
    },
    4: {
        "title_hi": "चौथी एएनसी विजिट", "title_en": "Fourth ANC Visit",
        "week_range": "36 Weeks",
        "items": [
            ("weight_measured", "वजन मापा गया", "Weight measured"),
            ("bp_checked", "बीपी जांची गई", "BP checked"),
            ("fetal_position_checked", "भ्रूण स्थिति जांच", "Fetal position checked"),
            ("delivery_planning_completed", "प्रसव योजना पूर्ण", "Delivery planning completed"),
            ("referral_facility_identified", "रेफरल सुविधा चयनित", "Referral facility identified"),
            ("emergency_transport_confirmed", "आपातकालीन परिवहन पुष्टि", "Emergency transport confirmed"),
        ],
    },
}

MEDICINE_DEFAULTS = {"iron": 180, "calcium": 360}
IMMUNIZATION_DOSE_TYPES = ["dose_1", "dose_2", "booster"]
ULTRASOUND_SCAN_TYPES = ["pregnancy_scan", "early_scan", "anomaly_scan", "growth_scan"]


class ImmunizationUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(pending|received)$")
    received_date: Optional[date] = None

class UltrasoundUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(due|completed)$")
    scan_date: Optional[date] = None
    facility_name: Optional[str] = None

class ChecklistUpdateRequest(BaseModel):
    item_key: str
    checked: bool

REGISTRATION_SELF_REPORT_FIELDS = ["is_registered", "rch_id_generated", "mcp_card_received"]

MATERNAL_NUTRITION_FIELDS = ["nutrition_counselling_received", "weight_monitored", "supplementary_nutrition_received"]

class MaternalNutritionFieldUpdateRequest(BaseModel):
    field: str
    checked: bool

    @field_validator("field")
    @classmethod
    def valid_field(cls, v: str) -> str:
        if v not in MATERNAL_NUTRITION_FIELDS:
            raise ValueError(f"Invalid field. Must be one of: {MATERNAL_NUTRITION_FIELDS}")
        return v

class RegistrationFieldUpdateRequest(BaseModel):
    field: str = Field(..., description="One of: is_registered, rch_id_generated, mcp_card_received")
    checked: bool

    @field_validator("field")
    @classmethod
    def valid_field(cls, v: str) -> str:
        if v not in REGISTRATION_SELF_REPORT_FIELDS:
            raise ValueError(f"Invalid field. Must be one of: {REGISTRATION_SELF_REPORT_FIELDS}")
        return v

class MedicineDateToggleRequest(BaseModel):
    taken: bool

# ── Appointments ──────────────────────────────────────────────────────────────

class AppointmentCreate(BaseModel):
    appointment_type: str = Field(..., pattern=r"^(anc|pnc|immunization|pmsma|other)$")
    scheduled_at: datetime
    facility_name: Optional[str] = None
    notes: Optional[str] = None


class AppointmentOut(BaseModel):
    id: UUID
    appointment_type: str
    scheduled_at: datetime
    facility_name: Optional[str]
    status: str
    notes: Optional[str]

    class Config:
        from_attributes = True


# ── Emergency / Danger Signs ───────────────────────────────────────────────────

DANGER_SIGNS = [
    "severe_headache",
    "blurred_vision",
    "severe_abdominal_pain",
    "heavy_bleeding",
    "high_fever",
    "difficulty_breathing",
    "reduced_fetal_movement",
    "swelling_face_hands",
    "unconsciousness",
    "convulsions",
    "water_broken_early",
    "other",
]


class DangerSignRequest(BaseModel):
    symptoms: list[str] = Field(..., min_length=1, description="List of danger sign codes")
    additional_notes: Optional[str] = None
    current_latitude: Optional[str] = None
    current_longitude: Optional[str] = None

    @field_validator("symptoms")
    @classmethod
    def valid_symptoms(cls, v: list[str]) -> list[str]:
        invalid = [s for s in v if s not in DANGER_SIGNS]
        if invalid:
            raise ValueError(f"Unknown symptom codes: {invalid}")
        return v


class EmergencyRequest(BaseModel):
    symptoms: list[str]
    additional_notes: Optional[str] = None
    current_latitude: Optional[str] = None
    current_longitude: Optional[str] = None
    call_ambulance: bool = True
    notify_asha: bool = True


class EmergencyResponse(BaseModel):
    alert_id: UUID
    alert_severity: str
    ambulance_number: str = "102"
    alternate_ambulance: str = "108"
    asha_notified: bool
    asha_name: Optional[str]
    asha_mobile: Optional[str]
    message_hi: str
    message_en: str
    nearby_facilities: list[dict]


# ── Reminders ─────────────────────────────────────────────────────────────────

class ReminderOut(BaseModel):
    id: UUID
    reminder_type: str
    message_hi: Optional[str]
    message_en: Optional[str]
    scheduled_at: datetime
    status: str
    channel: str

    class Config:
        from_attributes = True


# ── Schemes ───────────────────────────────────────────────────────────────────

class SchemeOut(BaseModel):
    id: UUID
    title_hi: str
    title_en: str
    content_hi: Optional[str]
    content_en: Optional[str]
    category: str
    tags: Optional[list[str]]
    is_eligible: Optional[bool] = None
    application_status: Optional[str] = None


class SchemeEligibilityResponse(BaseModel):
    jsy: bool
    jssk: bool
    pmsma: bool
    pmmvy: bool
    minimata: bool
    eligibility_reasons: dict[str, str]


# ── Chatbot ───────────────────────────────────────────────────────────────────

class ChatbotMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    language: str = Field("hi", pattern=r"^(hi|en)$")


class ChatbotMessageResponse(BaseModel):
    reply: str
    language: str
    sources: Optional[list[str]] = None
    suggested_questions: Optional[list[str]] = None


class ChatbotFeedbackRequest(BaseModel):
    message_index: int
    is_helpful: bool
    comment: Optional[str] = None


# ── FAQs ──────────────────────────────────────────────────────────────────────

class FAQOut(BaseModel):
    id: UUID
    category: str
    subcategory: Optional[str] = None
    title_hi: Optional[str] = None
    title_en: Optional[str] = None
    content_hi: Optional[str] = None
    content_en: Optional[str] = None
    tags: Optional[list[str]] = None
 
    class Config:
        from_attributes = True 


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: UUID
    title: str
    body: str
    type: str
    is_read: bool
    created_at: datetime
    data: Optional[dict] = None
    

# ── Symptom Checker ───────────────────────────────────────────────────────────
class SymptomCheckRequest(BaseModel):
    symptom: Optional[str] = None
    lang: str = Field("hi", pattern=r"^(hi|en)$")

# ── Nutrition ─────────────────────────────────────────────────────────────────
class NutritionResponse(BaseModel):
    trimester: int
    week: int
    title: str
    key_nutrients: list[dict]
    foods_to_eat: list[str]
    foods_to_avoid: list[str]
    daily_calories: str
    water_intake: str
    tip: str

# ── Health Dashboard ──────────────────────────────────────────────────────────
class HealthDashboardResponse(BaseModel):
    pregnancy: dict
    vitals: dict
    anc: dict
    bpcr: dict
    health_flags: list[dict]
    active_alerts: int
    reminders: dict

# ── Tests ─────────────────────────────────────────────────────────────────────
class TestItem(BaseModel):
    name: str
    display_name: str
    when: str
    is_mandatory: bool

# ── Medicine Reminder ─────────────────────────────────────────────────────────
class MedicineReminderCreate(BaseModel):
    medicine_name: str
    dose: str
    time_of_day: str = Field(..., pattern=r"^(morning|afternoon|evening|night)$")
    notes: Optional[str] = None
