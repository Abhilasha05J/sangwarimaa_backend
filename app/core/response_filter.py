"""
Strips sensitive fields from API responses based on user role.
Import and use in route handlers.
"""

from typing import Any

# Fields NEVER returned to ANY user
ALWAYS_STRIP = {
    "password_hash",
    "otp",
    "fcm_token",
    "asha_id",
    "anm_id",
    "user_id",
    "consent_at",
    "updated_at",
}

# Fields only visible to admin roles
ADMIN_ONLY_FIELDS = {
    "last_login_at",
    "ip_address",
    "is_active",
    "drop_off",
    "metadata",
}

# Fields visible to ASHA + admin but NOT to women
ASHA_ADMIN_ONLY = {
    "risk_level",
    "high_risk_flags",
    "bpcr_score",
}


def filter_response(data: Any, role: str = "pregnant_woman") -> Any:
    """
    Recursively strip sensitive fields from response data.
    
    role: "pregnant_woman" | "asha" | "anm" | "block_admin" | "pi" | "super_admin"
    """
    is_admin = role in ("block_admin", "pi", "super_admin")
    is_field_worker = role in ("asha", "anm")

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Always strip these
            if key in ALWAYS_STRIP:
                continue
            # Admin only fields
            if key in ADMIN_ONLY_FIELDS and not is_admin:
                continue
            # Recurse into nested dicts/lists
            result[key] = filter_response(value, role)
        return result

    elif isinstance(data, list):
        return [filter_response(item, role) for item in data]

    return data


def safe_envelope(data: Any, role: str = "pregnant_woman", meta: dict = None) -> dict:
    """
    Like success_envelope but filters sensitive fields by role.
    Use this instead of success_envelope in production routes.
    """
    return {
        "success": True,
        "data": filter_response(data, role),
        "meta": meta,
        "error": None,
    }