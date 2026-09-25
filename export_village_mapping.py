"""
Read-only export for client/admin review: for every village, shows the
roster SHC name next to whatever facility it actually resolved to in the
database, plus match status. Writes a CSV and also prints the rows that
need a human decision (status != 'exact').

Does NOT write to the database — safe to run anytime, as many times as
you like.

Run once from backend folder:
    python export_village_mapping.py
Note: the roster xlsx and this script (and seed_villages.py, imported for
its parser) should be in the same folder.
"""

import asyncio
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import HealthFacility, Village
from seed_villages import parse_roster, ROSTER_FILE

OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "village_shc_mapping_review.csv")


async def export():
    print("Reading roster Excel...")
    villages, _workers, _shc_to_phc, _phc_to_chc = parse_roster(ROSTER_FILE)
    print(f"Parsed {len(villages)} village rows.")

    async with AsyncSessionLocal() as db:
        facility_res = await db.execute(select(HealthFacility))
        facility_by_id = {f.id: f for f in facility_res.scalars().all()}

        rows = []
        for v in villages:
            code = v["code"]
            if code:
                res = await db.execute(select(Village).where(Village.code == code))
            else:
                res = await db.execute(
                    select(Village).where(Village.name == v["name"], Village.block == v["block"])
                )
            db_village = res.scalar_one_or_none()

            matched_facility = None
            match_status = "NOT FOUND IN DB"
            if db_village:
                match_status = db_village.match_status
                if db_village.shc_id:
                    matched_facility = facility_by_id.get(db_village.shc_id)

            rows.append({
                "village_name": v["name"],
                "village_code": code or "",
                "block": v["block"],
                "roster_shc_name": v["shc_roster_name"],
                "match_status": match_status,
                "matched_facility_name": matched_facility.name if matched_facility else "UNRESOLVED",
            })

        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")

        needs_review = [r for r in rows if r["match_status"] != "exact"]
        print(f"\n{len(needs_review)} rows need review (status != 'exact'):\n")
        print(f"{'village_name':<25} {'block':<10} {'roster_shc_name':<22} {'status':<12} {'matched_facility_name'}")
        print("-" * 100)
        for r in needs_review:
            print(f"{r['village_name']:<25} {r['block']:<10} {r['roster_shc_name']:<22} "
                  f"{r['match_status']:<12} {r['matched_facility_name']}")


if __name__ == "__main__":
    asyncio.run(export())
