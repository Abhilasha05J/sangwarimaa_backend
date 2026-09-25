# import asyncio
# import sys
# import os

# # Add project root to path
# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# from openpyxl import load_workbook
# from app.core.database import AsyncSessionLocal
# from app.models.models import HealthFacility


# # ── Approximate coordinates for Raipur sub-districts ─────────────────────────
# # In production, geocode these via Google Maps API
# SUBDISTRICT_COORDS = {
#     "Raipur":    (21.2514, 81.6296),
#     "Birgaon":   (21.2200, 81.7200),
#     "Abhanpur":  (20.9300, 81.6800),
#     "Tilda":     (21.3500, 81.9000),
#     "Arang":     (21.1900, 81.9700),
#     "Dharsiwa":  (21.1500, 81.7500),
# }

# FACILITY_TYPE_MAP = {
#     "Medical College":          "MC",
#     "Community Health Centre":  "CHC",
#     "Primary Health Centre":    "PHC",
#     "Health Sub Centre":        "SHC",
#     "District Hospital":        "DH",
#     "Sub District Hospital":    "SDH",
# }

# # Facilities that have 24x7 delivery services
# ALWAYS_OPEN = {"24X7", "24x7"}

# # Facilities that are FRUs (First Referral Units)
# FRU_FACILITIES = {"DH RAIPUR", "CHC Birgaon", "CHC Kharora", "CHC ABHANPUR"}


# def is_24x7(category: str) -> bool:
#     if not category:
#         return False
#     return any(tag in str(category).upper() for tag in ALWAYS_OPEN)


# def is_functional(category: str) -> bool:
#     if not category:
#         return True
#     return "Non functional" not in str(category)


# def get_coords(subdistrict: str):
#     return SUBDISTRICT_COORDS.get(subdistrict, (21.2514, 81.6296))


# def parse_excel(filepath: str) -> list[dict]:
#     wb = load_workbook(filepath, read_only=True)
#     facilities = []

#     # ── MC Sheet ──────────────────────────────────────────────────────────────
#     ws = wb["MC"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:  # Facility Name
#             continue
#         lat, lon = get_coords(str(row[2] or "Raipur"))
#         facilities.append({
#             "name": str(row[4]).strip(),
#             "facility_type": "MC",
#             "district": "Raipur",
#             "sub_district": str(row[2] or "Raipur").strip(),
#             "block": str(row[3] or "").strip() or None,
#             "rural_urban": str(row[5] or "Urban").strip(),
#             "is_fru": False,
#             "is_24x7": True,
#             "has_labour_room": True,
#             "has_blood_bank": True,
#             "is_functional": True,
#             "anc_registrations": 0,
#             "category": None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": str(row[6] or "").strip() or None,
#         })

#     # ── DH Sheet ──────────────────────────────────────────────────────────────
#     ws = wb["DH"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:
#             continue
#         lat, lon = get_coords(str(row[2] or "Raipur"))
#         name = str(row[4]).strip()
#         facilities.append({
#             "name": name,
#             "facility_type": "DH",
#             "district": "Raipur",
#             "sub_district": str(row[2] or "Raipur").strip(),
#             "block": str(row[3] or "").strip() or None,
#             "rural_urban": str(row[5] or "Urban").strip(),
#             "is_fru": str(row[6] or "").upper() == "YES",
#             "is_24x7": True,
#             "has_labour_room": True,
#             "has_blood_bank": True,
#             "is_functional": True,
#             "anc_registrations": 0,
#             "category": None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": str(row[7] or "").strip() or None,
#         })

#     # ── SDH Sheet ─────────────────────────────────────────────────────────────
#     ws = wb["SDH"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:
#             continue
#         lat, lon = get_coords(str(row[2] or "Raipur"))
#         facilities.append({
#             "name": str(row[4]).strip(),
#             "facility_type": "SDH",
#             "district": "Raipur",
#             "sub_district": str(row[2] or "Raipur").strip(),
#             "block": str(row[3] or "").strip() or None,
#             "rural_urban": str(row[5] or "Rural").strip(),
#             "is_fru": str(row[6] or "No").upper() == "YES",
#             "is_24x7": True,
#             "has_labour_room": True,
#             "has_blood_bank": False,
#             "is_functional": True,
#             "anc_registrations": 0,
#             "category": None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": None,
#         })

#     # ── CHC Sheet ─────────────────────────────────────────────────────────────
#     ws = wb["DP NoV CHC"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:
#             continue
#         subdistrict = str(row[2] or "Raipur").strip()
#         lat, lon = get_coords(subdistrict)
#         name = str(row[4]).strip()
#         category = str(row[5] or "").strip()
#         anc = int(row[6]) if row[6] and str(row[6]).isdigit() else 0
#         facilities.append({
#             "name": name,
#             "facility_type": "CHC",
#             "district": "Raipur",
#             "sub_district": subdistrict,
#             "block": subdistrict,
#             "rural_urban": "Urban" if subdistrict == "Raipur" else "Rural",
#             "is_fru": name in FRU_FACILITIES,
#             "is_24x7": True,
#             "has_labour_room": True,
#             "has_blood_bank": name in FRU_FACILITIES,
#             "is_functional": True,
#             "anc_registrations": anc,
#             "category": category or None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": None,
#         })

#     # ── PHC Sheet ─────────────────────────────────────────────────────────────
#     ws = wb["DP NoV PHC"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:
#             continue
#         subdistrict = str(row[2] or "Raipur").strip()
#         lat, lon = get_coords(subdistrict)
#         category = str(row[5] or "").strip()
#         anc = int(row[6]) if row[6] and str(row[6]).isdigit() else 0
#         facilities.append({
#             "name": str(row[4]).strip(),
#             "facility_type": "PHC",
#             "district": "Raipur",
#             "sub_district": subdistrict,
#             "block": subdistrict,
#             "rural_urban": "Urban" if "UPHC" in str(row[4]) else "Rural",
#             "is_fru": False,
#             "is_24x7": is_24x7(category),
#             "has_labour_room": is_24x7(category),
#             "has_blood_bank": False,
#             "is_functional": True,
#             "anc_registrations": anc,
#             "category": category or None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": None,
#         })

#     # ── SHC Sheet ─────────────────────────────────────────────────────────────
#     ws = wb["DP NoV SHC"]
#     for row in ws.iter_rows(min_row=3, values_only=True):
#         if not row[4]:
#             continue
#         subdistrict = str(row[2] or "Raipur").strip()
#         lat, lon = get_coords(subdistrict)
#         category = str(row[5] or "").strip()
#         facilities.append({
#             "name": str(row[4]).strip(),
#             "facility_type": "SHC",
#             "district": "Raipur",
#             "sub_district": subdistrict,
#             "block": subdistrict,
#             "rural_urban": "Rural",
#             "is_fru": False,
#             "is_24x7": False,
#             "has_labour_room": False,
#             "has_blood_bank": False,
#             "is_functional": is_functional(category),
#             "anc_registrations": 0,
#             "category": category or None,
#             "latitude": str(lat),
#             "longitude": str(lon),
#             "remarks": None,
#         })

#     return facilities


# async def seed():
#     print("📂 Reading Excel...")
#     facilities = parse_excel(
#        os.path.join(os.path.dirname(__file__), "Raipur Facility_List 2024-25 09.06.2026.xlsx")
#     )
#     print(f"✅ Parsed {len(facilities)} facilities")

#     print("🗄️  Connecting to database...")
#     async with AsyncSessionLocal() as db:
#         # Check if already seeded
#         from sqlalchemy import select, func
#         count_res = await db.execute(select(func.count(HealthFacility.id)))
#         existing = count_res.scalar() or 0

#         if existing > 0:
#             print(f"⚠️  {existing} facilities already in DB.")
#             answer = input("Re-seed? This will DELETE existing facilities. (yes/no): ")
#             if answer.strip().lower() != "yes":
#                 print("Skipped.")
#                 return

#             # Delete existing
#             from sqlalchemy import delete
#             await db.execute(delete(HealthFacility))
#             await db.commit()
#             print("🗑️  Cleared existing facilities")

#         # Insert all
#         for f in facilities:
#             facility = HealthFacility(**f)
#             db.add(facility)

#         await db.commit()
#         print(f"✅ Seeded {len(facilities)} facilities into health_facilities table!")

#         # Summary
#         from sqlalchemy import select
#         for ftype in ["MC", "DH", "SDH", "CHC", "PHC", "SHC"]:
#             res = await db.execute(
#                 select(func.count(HealthFacility.id))
#                 .where(HealthFacility.facility_type == ftype)
#             )
#             count = res.scalar() or 0
#             print(f"   {ftype}: {count}")


# if __name__ == "__main__":
#     asyncio.run(seed())
"""
Seed health facilities from Raipur Excel into health_facilities table.
Run once from backend folder:
    python seed_facilities.py
Note. - the excel file and seed_facilities.py should be in the same folder

Safe to re-run: facilities are upserted by (name, facility_type) instead of
being deleted and re-inserted, so re-running this after the client sends an
updated facility file won't wipe out parent_facility_id links set by
seed_villages.py, or any admin edits made later.
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openpyxl import load_workbook
from app.core.database import AsyncSessionLocal
from app.models.models import HealthFacility


# ── Approximate coordinates for Raipur sub-districts ─────────────────────────
# In production, geocode these via Google Maps API
# NOTE: every facility in a sub-district gets the SAME centroid here — this
# is a placeholder, not a real location. Don't build "nearest facility by
# distance" on top of these without real per-facility geocoding.
SUBDISTRICT_COORDS = {
    "Raipur":    (21.2514, 81.6296),
    "Birgaon":   (21.2200, 81.7200),
    "Abhanpur":  (20.9300, 81.6800),
    "Tilda":     (21.3500, 81.9000),
    "Arang":     (21.1900, 81.9700),
    "Dharsiwa":  (21.1500, 81.7500),
}

FACILITY_TYPE_MAP = {
    "Medical College":          "MC",
    "Community Health Centre":  "CHC",
    "Primary Health Centre":    "PHC",
    "Health Sub Centre":        "SHC",
    "District Hospital":        "DH",
    "Sub District Hospital":    "SDH",
}

# Facilities that have 24x7 delivery services
ALWAYS_OPEN = {"24X7", "24x7"}

# Facilities that are FRUs (First Referral Units)
# NOTE: hardcoded — the CHC sheet has no FRU column, so this list is a
# guess, not read from data. Worth confirming with the client.
FRU_FACILITIES = {"DH RAIPUR", "CHC Birgaon", "CHC Kharora", "CHC ABHANPUR"}


def is_24x7(category: str) -> bool:
    if not category:
        return False
    return any(tag in str(category).upper() for tag in ALWAYS_OPEN)


def is_functional(category: str) -> bool:
    if not category:
        return True
    return "Non functional" not in str(category)


def get_coords(subdistrict: str):
    return SUBDISTRICT_COORDS.get(subdistrict, (21.2514, 81.6296))


def parse_excel(filepath: str) -> list[dict]:
    wb = load_workbook(filepath, read_only=True)
    facilities = []

    # ── MC Sheet ──────────────────────────────────────────────────────────────
    ws = wb["MC"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:  # Facility Name
            continue
        lat, lon = get_coords(str(row[2] or "Raipur"))
        facilities.append({
            "name": str(row[4]).strip(),
            "facility_type": "MC",
            "district": "Raipur",
            "sub_district": str(row[2] or "Raipur").strip(),
            "block": str(row[3] or "").strip() or None,
            "rural_urban": str(row[5] or "Urban").strip(),
            "is_fru": False,
            "is_24x7": True,
            "has_labour_room": True,
            "has_blood_bank": True,
            "is_functional": True,
            "anc_registrations": 0,
            "category": None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": str(row[6] or "").strip() or None,
        })

    # ── DH Sheet ──────────────────────────────────────────────────────────────
    ws = wb["DH"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:
            continue
        lat, lon = get_coords(str(row[2] or "Raipur"))
        name = str(row[4]).strip()
        facilities.append({
            "name": name,
            "facility_type": "DH",
            "district": "Raipur",
            "sub_district": str(row[2] or "Raipur").strip(),
            "block": str(row[3] or "").strip() or None,
            "rural_urban": str(row[5] or "Urban").strip(),
            "is_fru": str(row[6] or "").upper() == "YES",
            "is_24x7": True,
            "has_labour_room": True,
            "has_blood_bank": True,
            "is_functional": True,
            "anc_registrations": 0,
            "category": None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": str(row[7] or "").strip() or None,
        })

    # ── SDH Sheet ─────────────────────────────────────────────────────────────
    ws = wb["SDH"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:
            continue
        lat, lon = get_coords(str(row[2] or "Raipur"))
        facilities.append({
            "name": str(row[4]).strip(),
            "facility_type": "SDH",
            "district": "Raipur",
            "sub_district": str(row[2] or "Raipur").strip(),
            "block": str(row[3] or "").strip() or None,
            "rural_urban": str(row[5] or "Rural").strip(),
            "is_fru": str(row[6] or "No").upper() == "YES",
            "is_24x7": True,
            "has_labour_room": True,
            "has_blood_bank": False,
            "is_functional": True,
            "anc_registrations": 0,
            "category": None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": None,
        })

    # ── CHC Sheet ─────────────────────────────────────────────────────────────
    ws = wb["DP NoV CHC"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:
            continue
        subdistrict = str(row[2] or "Raipur").strip()
        lat, lon = get_coords(subdistrict)
        name = str(row[4]).strip()
        category = str(row[5] or "").strip()
        anc = int(row[6]) if row[6] and str(row[6]).isdigit() else 0
        facilities.append({
            "name": name,
            "facility_type": "CHC",
            "district": "Raipur",
            "sub_district": subdistrict,
            "block": subdistrict,
            "rural_urban": "Urban" if subdistrict == "Raipur" else "Rural",
            "is_fru": name in FRU_FACILITIES,
            "is_24x7": True,
            "has_labour_room": True,
            "has_blood_bank": name in FRU_FACILITIES,
            "is_functional": True,
            "anc_registrations": anc,
            "category": category or None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": None,
        })

    # ── PHC Sheet ─────────────────────────────────────────────────────────────
    ws = wb["DP NoV PHC"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:
            continue
        subdistrict = str(row[2] or "Raipur").strip()
        lat, lon = get_coords(subdistrict)
        category = str(row[5] or "").strip()
        anc = int(row[6]) if row[6] and str(row[6]).isdigit() else 0
        facilities.append({
            "name": str(row[4]).strip(),
            "facility_type": "PHC",
            "district": "Raipur",
            "sub_district": subdistrict,
            "block": subdistrict,
            "rural_urban": "Urban" if "UPHC" in str(row[4]) else "Rural",
            "is_fru": False,
            "is_24x7": is_24x7(category),
            "has_labour_room": is_24x7(category),
            "has_blood_bank": False,
            "is_functional": True,
            "anc_registrations": anc,
            "category": category or None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": None,
        })

    # ── SHC Sheet ─────────────────────────────────────────────────────────────
    ws = wb["DP NoV SHC"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row[4]:
            continue
        subdistrict = str(row[2] or "Raipur").strip()
        lat, lon = get_coords(subdistrict)
        category = str(row[5] or "").strip()
        facilities.append({
            "name": str(row[4]).strip(),
            "facility_type": "SHC",
            "district": "Raipur",
            "sub_district": subdistrict,
            "block": subdistrict,
            "rural_urban": "Rural",
            "is_fru": False,
            "is_24x7": False,
            "has_labour_room": False,
            "has_blood_bank": False,
            "is_functional": is_functional(category),
            "anc_registrations": 0,
            "category": category or None,
            "latitude": str(lat),
            "longitude": str(lon),
            "remarks": None,
        })

    return facilities


async def seed():
    print("📂 Reading Excel...")
    facilities = parse_excel(
       os.path.join(os.path.dirname(__file__), "Raipur Facility_List 2024-25 09.06.2026.xlsx")
    )
    print(f"✅ Parsed {len(facilities)} facilities")

    print("🗄️  Connecting to database...")
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select, func

        count_res = await db.execute(select(func.count(HealthFacility.id)))
        existing = count_res.scalar() or 0
        if existing > 0:
            print(f"ℹ️  {existing} facilities already in DB — upserting by (name, facility_type).")

        inserted = 0
        updated = 0
        for f in facilities:
            res = await db.execute(
                select(HealthFacility).where(
                    HealthFacility.name == f["name"],
                    HealthFacility.facility_type == f["facility_type"],
                )
            )
            existing_row = res.scalar_one_or_none()
            if existing_row:
                for k, v in f.items():
                    setattr(existing_row, k, v)
                updated += 1
            else:
                db.add(HealthFacility(**f))
                inserted += 1

        await db.commit()
        print(f"✅ Facilities upserted: {inserted} inserted, {updated} updated.")

        # Summary
        for ftype in ["MC", "DH", "SDH", "CHC", "PHC", "SHC"]:
            res = await db.execute(
                select(func.count(HealthFacility.id))
                .where(HealthFacility.facility_type == ftype)
            )
            count = res.scalar() or 0
            print(f"   {ftype}: {count}")


if __name__ == "__main__":
    asyncio.run(seed())