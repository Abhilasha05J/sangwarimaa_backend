import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.dialects.postgresql import UUID, JSONB


from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey,
    Integer, JSON, SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


# ── Enums ────────────────────────────────────────────────────────────────────

import enum

class UserRole(str, enum.Enum):
    pregnant_woman = "pregnant_woman"
    asha = "asha"
    anm = "anm"
    block_admin = "block_admin"
    pi = "pi"
    super_admin = "super_admin"

class RiskLevel(str, enum.Enum):
    low = "low"
    yellow = "yellow"
    red = "red"

class AlertStatus(str, enum.Enum):
    pending = "pending"
    contacted = "contacted"
    referred = "referred"
    resolved = "resolved"
    closed = "closed"

class AlertType(str, enum.Enum):
    danger_sign = "danger_sign"
    missed_anc = "missed_anc"
    bpcr_low = "bpcr_low"
    custom = "custom"

class ReminderStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"

class LangPref(str, enum.Enum):
    hi = "hi"
    en = "en"


# ── Models ────────────────────────────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mobile = Column(String(15), unique=True, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    name = Column(Text)
    preferred_language = Column(Enum(LangPref), default=LangPref.hi)
    fcm_token = Column(Text)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    beneficiary = relationship("Beneficiary", back_populates="user", uselist=False)
    field_worker = relationship("FieldWorker", back_populates="user", uselist=False)


class AdminCredential(Base):
    __tablename__ = "admin_credentials"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email = Column(String(255), unique=True)
    password_hash = Column(Text, nullable=False)


class FieldWorker(Base):
    __tablename__ = "field_workers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    worker_role = Column(String(10), nullable=False)   # 'asha' | 'anm'
    subcentre = Column(Text)
    village = Column(Text)
    block = Column(Text, nullable=False)
    district = Column(Text, nullable=False)
    unique_id = Column(Text, unique=True)
    latitude = Column(String(20))
    longitude = Column(String(20))

    user = relationship("User", back_populates="field_worker")
    beneficiaries_as_asha = relationship("Beneficiary", foreign_keys="Beneficiary.asha_id", back_populates="asha_worker")
    beneficiaries_as_anm = relationship("Beneficiary", foreign_keys="Beneficiary.anm_id", back_populates="anm_worker")
    alerts_assigned = relationship("Alert", back_populates="assigned_worker")


class Beneficiary(Base):
    __tablename__ = "beneficiaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name = Column(Text, nullable=False)
    age = Column(Integer)
    husband_name = Column(Text)
    husband_age = Column(Integer)
    dob = Column(Date)
    address = Column(Text)
    village = Column(Text)
    phc = Column(Text)
    block = Column(Text)
    district = Column(Text)
    lmp = Column(Date, nullable=False)                 # Last menstrual period
    blood_group = Column(String(5))
    consent = Column(Boolean, default=False)
    consent_at = Column(DateTime(timezone=True))
    asha_id = Column(UUID(as_uuid=True), ForeignKey("field_workers.id"))
    anm_id = Column(UUID(as_uuid=True), ForeignKey("field_workers.id"))
    risk_level = Column(Enum(RiskLevel), default=RiskLevel.low)
    latitude = Column(String(20))
    longitude = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    user = relationship("User", back_populates="beneficiary")
    asha_worker = relationship("FieldWorker", foreign_keys=[asha_id], back_populates="beneficiaries_as_asha")
    anm_worker = relationship("FieldWorker", foreign_keys=[anm_id], back_populates="beneficiaries_as_anm")
    bpcr_assessments = relationship("BPCRAssessment", back_populates="beneficiary", cascade="all, delete-orphan")
    anc_visits = relationship("ANCVisit", back_populates="beneficiary", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="beneficiary", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="beneficiary", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="beneficiary", cascade="all, delete-orphan")


class BPCRAssessment(Base):
    __tablename__ = "bpcr_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    assessed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    component = Column(Text, nullable=False)        # e.g. "birth_place", "transport", "funds"
    score = Column(SmallInteger)                    # 0 or 1
    response = Column(JSONB)
    assessed_at = Column(DateTime(timezone=True), default=now_utc)

    beneficiary = relationship("Beneficiary", back_populates="bpcr_assessments")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    triggered_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    alert_type = Column(Enum(AlertType), nullable=False)
    severity = Column(Enum(RiskLevel), nullable=False)
    symptoms = Column(JSONB)
    notes = Column(Text)
    status = Column(Enum(AlertStatus), default=AlertStatus.pending)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("field_workers.id"))
    triggered_at = Column(DateTime(timezone=True), default=now_utc)
    responded_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))

    beneficiary = relationship("Beneficiary", back_populates="alerts")
    assigned_worker = relationship("FieldWorker", back_populates="alerts_assigned")


class ANCVisit(Base):
    __tablename__ = "anc_visits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("field_workers.id"))
    visit_date = Column(Date, nullable=False)
    visit_number = Column(SmallInteger)
    weight_kg = Column(String(8))
    bp_systolic = Column(SmallInteger)
    bp_diastolic = Column(SmallInteger)
    hemoglobin = Column(String(6))
    fundal_height = Column(String(6))
    fetal_heart_rate = Column(SmallInteger)
    notes = Column(Text)
    next_due_date = Column(Date)
    checklist = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    beneficiary = relationship("Beneficiary", back_populates="anc_visits")

class PregnancyRegistration(Base):
    """1:1 with Beneficiary — RCH/MCP registration status shown on ANC Services screen."""
    __tablename__ = "pregnancy_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), unique=True, nullable=False)
    is_registered = Column(Boolean, default=False)
    registered_date = Column(Date)
    rch_id = Column(Text)
    rch_id_generated = Column(Boolean, default=False)
    mcp_card_received = Column(Boolean, default=False)
    mcp_card_received_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    beneficiary = relationship("Beneficiary")

class MaternalNutrition(Base):
    """1:1 with Beneficiary — distinct from PregnancyRegistration, self-reported by the woman."""
    __tablename__ = "maternal_nutrition"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), unique=True, nullable=False)
    nutrition_counselling_received = Column(Boolean, default=False)
    weight_monitored = Column(Boolean, default=False)
    supplementary_nutrition_received = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    beneficiary = relationship("Beneficiary")

class MedicineTracker(Base):
    """One row per (beneficiary, medicine_type) — 'iron' | 'calcium' adherence counter."""
    __tablename__ = "medicine_trackers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    medicine_type = Column(Text, nullable=False)          # 'iron' | 'calcium'
    total_doses = Column(Integer, nullable=False, default=180)
    doses_taken = Column(Integer, nullable=False, default=0)
    last_taken_date = Column(Date)
    started_at = Column(Date, default=lambda: datetime.now(timezone.utc).date())
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    __table_args__ = (UniqueConstraint("beneficiary_id", "medicine_type", name="uq_medicine_tracker"),)
    beneficiary = relationship("Beneficiary")

class MedicineIntakeLog(Base):
    """One row per date a dose was marked taken — powers the calendar view."""
    __tablename__ = "medicine_intake_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    medicine_type = Column(Text, nullable=False)
    taken_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (UniqueConstraint("beneficiary_id", "medicine_type", "taken_date", name="uq_medicine_intake_date"),)
    beneficiary = relationship("Beneficiary")

class Immunization(Base):
    """One row per (beneficiary, dose_type) — 'dose_1' | 'dose_2' | 'booster'."""
    __tablename__ = "immunizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    dose_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")   # 'pending' | 'received'
    received_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    __table_args__ = (UniqueConstraint("beneficiary_id", "dose_type", name="uq_immunization_dose"),)
    beneficiary = relationship("Beneficiary")


class UltrasoundScan(Base):
    """One row per (beneficiary, scan_type)."""
    __tablename__ = "ultrasound_scans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    scan_type = Column(Text, nullable=False)     # 'pregnancy_scan'|'early_scan'|'anomaly_scan'|'growth_scan'
    status = Column(Text, nullable=False, default="due")   # 'due' | 'completed'
    scan_date = Column(Date)
    facility_name = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    __table_args__ = (UniqueConstraint("beneficiary_id", "scan_type", name="uq_ultrasound_scan"),)
    beneficiary = relationship("Beneficiary")

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    appointment_type = Column(Text, nullable=False)  # 'anc','pnc','immunization','pmsma'
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    facility_name = Column(Text)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("field_workers.id"))
    status = Column(String(20), default="scheduled")
    notes = Column(Text)

    beneficiary = relationship("Beneficiary", back_populates="appointments")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiary_id = Column(UUID(as_uuid=True), ForeignKey("beneficiaries.id", ondelete="CASCADE"), nullable=False)
    reminder_type = Column(Text, nullable=False)
    message_hi = Column(Text)
    message_en = Column(Text)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    sent_at = Column(DateTime(timezone=True))
    status = Column(Enum(ReminderStatus), default=ReminderStatus.pending)
    channel = Column(String(10), default="fcm")

    beneficiary = relationship("Beneficiary", back_populates="reminders")


class OTPToken(Base):
    __tablename__ = "otp_tokens"

    mobile = Column(String(15), primary_key=True)
    otp = Column(String(6), nullable=False)
    attempts = Column(SmallInteger, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)


# class EducationalContent(Base):
#     __tablename__ = "educational_content"

#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     category = Column(Text, nullable=False)   # 'faq','video','scheme','danger_sign'
#     title_hi = Column(Text)
#     title_en = Column(Text)
#     content_hi = Column(Text)
#     content_en = Column(Text)
#     media_url = Column(Text)
#     tags = Column(JSONB)
#     is_active = Column(Boolean, default=True)
#     created_at = Column(DateTime(timezone=True), default=now_utc)

class EducationalContent(Base):
    __tablename__ = "educational_content"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category = Column(Text, nullable=False, index=True)      # 'bpcr', 'pnc' — add index, you'll filter by this constantly
    content_type = Column(Text, nullable=False, default="video")  # 'video' now, 'article'/'infographic' later — table name is generic, so future-proof it
    title_hi = Column(Text)
    title_en = Column(Text)
    content_hi = Column(Text)
    content_en = Column(Text)
    media_url = Column(Text, nullable=False)                  # nullable=False — a video row without a link is useless
    thumbnail_url = Column(Text)                               # NEW — YouTube auto-thumbnails are unreliable for youtu.be short links; store explicitly
    video_source = Column(Text, default="youtube")             # NEW — 'youtube', 'vimeo' — lets you decide embed-player vs external-link later
    duration_seconds = Column(Integer)                         # NEW, nullable — nice-to-have for UI, cheap to add now
    sort_order = Column(Integer, default=0)                    # NEW — lets Admin control display order instead of relying on created_at
    tags = Column(JSONB)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)  # NEW — you'll want this once Admin can edit rows

    
class ChatbotConversation(Base):
    __tablename__ = "chatbot_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    messages = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class AuditLog(Base):
    __tablename__ = "audit_logs"
 
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(Text, nullable=False)
    resource = Column(Text)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    ip_address = Column(Text)
    audit_metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_utc)

class HealthFacility(Base):
    __tablename__ = "health_facilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    facility_type = Column(String(10), nullable=False)   # MC|DH|SDH|CHC|PHC|SHC
    district = Column(Text, nullable=False)
    sub_district = Column(Text)
    block = Column(Text)
    rural_urban = Column(String(10))                     # Rural | Urban
    is_fru = Column(Boolean, default=False)              # First Referral Unit
    is_24x7 = Column(Boolean, default=False)
    has_labour_room = Column(Boolean, default=False)
    has_blood_bank = Column(Boolean, default=False)
    is_functional = Column(Boolean, default=True)
    phone = Column(Text)
    anc_registrations = Column(Integer, default=0)      # From HMIS data
    category = Column(Text)                              # 24X7 AAM-PHC etc.
    latitude = Column(String(20))
    longitude = Column(String(20))
    remarks = Column(Text)
    last_updated = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    created_at = Column(DateTime(timezone=True), default=now_utc)

class FAQ(Base):
    """
    Dedicated FAQ table — split out from EducationalContent because that
    table's `category` column means *content type* ('faq'/'video'/'scheme'/
    'danger_sign'), not topic. Here, `category` IS the topic
    ('bpcr' | 'mhs' | 'pnc'), which is what the GET /women/faqs?category=
    query param actually needs to filter on.
    """
    __tablename__ = "faqs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Top-level topic: 'bpcr' | 'mhs' | 'pnc' — drives the category chips.
    category = Column(Text, nullable=False, index=True)

    # Optional finer-grained grouping within a category, e.g. for PNC:
    # 'maternal_recovery' | 'nutrition' | 'mental_health' |
    # 'family_planning' | 'newborn_care' | 'newborn_danger_signs' |
    # 'immunization' | 'low_birth_weight'. Null for bpcr/mhs (single-section).
    subcategory = Column(Text, nullable=True)

    title_hi = Column(Text)
    title_en = Column(Text)
    content_hi = Column(Text)
    content_en = Column(Text)

    # Free-form search keywords — separate from category/subcategory.
    tags = Column(JSONB, default=list)

    display_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)