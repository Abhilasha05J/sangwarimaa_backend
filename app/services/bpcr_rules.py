"""
Pure functions: no DB and no app imports, so they can be unit-tested alone.
Source of truth is the client's BPCR doc: 14 domains, 100 points, each domain
is tick/no-tick (full weight or zero).

Answer shapes the Flutter app must send (stored as JSON in bpcr_answers):

transport = {
  "delivery_place": "government" | "private" | "undecided",
  "hospital_name": str,
  "government_ambulance": {"shared_with_family": bool},
  "private_ambulance": {"name": str, "phone": str},
  "own_vehicle": {"type": "car" | "bike" | "other", "driver_name": str, "driver_phone": str},
  "birth_companion": {"who": "asha" | "husband" | "other_family",
                      "name": str, "phone": str, "relation": str},
}
saved_money = {
  "self_saving": bool | null,
  "family_saving": {"husband": {"selected": bool, "name": str, "phone": str, "relation": str},
                    "mother_in_law": {"selected": bool, "name": str, "phone": str, "relation": str}},
}
community_financial_support = {"panch": bool|null, "sarpanch": ..., "healers": ..., "school_teachers": ..., "other": ...}
delivery_bag = {"baby_essentials": bool, "mother_clean_clothes": bool, "mcp_card_file": bool}
"""

DOMAINS = [
    ("pregnancy_registration", 5),
    ("anc_completion", 10),
    ("facility_identified", 10),
    ("sba_identified", 5),
    ("transport_plan", 10),
    ("backup_transport", 5),
    ("birth_companion", 5),
    ("emergency_contacts", 5),
    ("financial_preparedness", 10),
    ("blood_group_known", 5),
    ("blood_donor_identified", 10),
    ("delivery_bag", 5),
    ("danger_signs_knowledge", 10),
    ("family_counselling", 5),
]

# No data source decided yet: always 0 and reported as "not_tracked_yet".
NOT_TRACKED = {"emergency_contacts", "danger_signs_knowledge", "family_counselling"}

BAND_MESSAGES_EN = {
    "excellent": "You are well prepared for childbirth. Continue attending ANC visits and complete any remaining tasks before your expected delivery date.",
    "good": "You have completed most BPCR activities. Review the pending items to achieve full preparedness.",
    "moderate": "Important preparations are still incomplete. Please work with your ASHA or healthcare provider to finish them as soon as possible.",
    "poor": "Your birth preparedness is below the recommended level. Immediate counselling is advised to improve readiness for delivery.",
    "high_risk": "Your BPCR score indicates inadequate preparedness. Contact your ASHA/ANM immediately and complete the essential BPCR steps without delay.",
}


def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


def transport_options_complete(transport) -> int:
    """How many of the 3 transport options are fully filled in."""
    t = transport or {}
    n = 0
    if (t.get("government_ambulance") or {}).get("shared_with_family") is True:
        n += 1
    p = t.get("private_ambulance") or {}
    if _filled(p.get("name")) and _filled(p.get("phone")):
        n += 1
    o = t.get("own_vehicle") or {}
    if _filled(o.get("type")) and _filled(o.get("driver_name")) and _filled(o.get("driver_phone")):
        n += 1
    return n


def birth_companion_done(transport) -> bool:
    c = (transport or {}).get("birth_companion") or {}
    who = c.get("who")
    if who in ("asha", "husband"):
        return True
    if who == "other_family":
        return _filled(c.get("name")) and _filled(c.get("phone"))
    return False


def financial_done(saved_money, community) -> bool:
    sm = saved_money or {}
    fs = sm.get("family_saving") or {}
    family = any((fs.get(k) or {}).get("selected") is True for k in ("husband", "mother_in_law"))
    community_yes = any(v is True for v in (community or {}).values())
    return sm.get("self_saving") is True or family or community_yes


def delivery_bag_done(bag) -> bool:
    b = bag or {}
    return all(b.get(k) is True for k in ("baby_essentials", "mother_clean_clothes", "mcp_card_file"))


def band_for(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "poor"
    return "high_risk"


def build_result(flags: dict) -> dict:
    domains, total, reachable = [], 0, 0
    for key, points in DOMAINS:
        if key in NOT_TRACKED:
            domains.append({"key": key, "max_points": points, "earned": 0, "status": "not_tracked_yet"})
            continue
        reachable += points
        done = bool(flags.get(key))
        earned = points if done else 0
        total += earned
        domains.append({"key": key, "max_points": points, "earned": earned,
                        "status": "completed" if done else "pending"})
    band = band_for(total)
    return {
        "total_score": total,
        "max_score": 100,
        "max_reachable_now": reachable,
        "band": band,
        "message_en": BAND_MESSAGES_EN[band],
        "domains": domains,
    }