# use this file for db changes
import asyncio
from sqlalchemy import text
from app.core.database import engine

STATEMENTS = [
    # --- earlier changes (idempotent, already applied) ---
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS husband_contact_no VARCHAR(15);",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS other_family_member_name TEXT;",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS other_family_member_relation VARCHAR(50);",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS family_contact_no VARCHAR(15);",

    # --- 1. block spelling: villages now use the same spelling as health_facilities / mitanin_directory ---
    "UPDATE villages SET block = 'Abhanpur', updated_at = now() WHERE block = 'ABHANPUR';",
    "UPDATE villages SET block = 'Dharsiwa', updated_at = now() WHERE block = 'DHARSEEVA';",
    # registration copies villages.block into beneficiaries.block, so keep existing rows consistent too
    "UPDATE beneficiaries SET block = 'Abhanpur' WHERE block = 'ABHANPUR';",
    "UPDATE beneficiaries SET block = 'Dharsiwa' WHERE block = 'DHARSEEVA';",

    # --- 2. PHC -> CHC parent links (source: CHC/PHC columns of the Mitanin list) ---
    # CHC ABHANPUR (96fc3ad5...) <- Champaran, Khilora, Khorpa, Manikchauri, Parshada, Torla, Uparwara
    """UPDATE health_facilities SET parent_facility_id = '96fc3ad5-177a-4438-8005-c90a62d355cf'
       WHERE facility_type = 'PHC' AND parent_facility_id IS DISTINCT FROM '96fc3ad5-177a-4438-8005-c90a62d355cf'
         AND id IN ('e598479b-0f31-4616-9104-436a7e74a635',
                    '4611445a-1546-43e9-9bc7-693413469b4d',
                    '38c16ea0-5924-4e08-bd28-03f82705e6e0',
                    '67fc7efb-a3e2-403a-90a7-a5913a4db962',
                    'e07226da-dc9e-45db-a516-10422a37c041',
                    '13996323-3a7e-4ee1-8986-8aea68feea5a',
                    '8fd043db-804b-4861-97be-0c50feccd02b');""",
    # CHC Dharsiwa (9fbbaf2c...) <- Dondekala, Mandhar, Silyari
    """UPDATE health_facilities SET parent_facility_id = '9fbbaf2c-9e0a-43d5-b567-1f04cda46cff'
       WHERE facility_type = 'PHC' AND parent_facility_id IS DISTINCT FROM '9fbbaf2c-9e0a-43d5-b567-1f04cda46cff'
         AND id IN ('82c15be1-fa26-4629-941d-73ccc41a0c6e',
                    '782cb102-e402-4d14-bb24-7a2c99e9b3d8',
                    '11bd3aaa-b59b-4ba4-b6af-27d90ef0f6cb');""",

    # --- 3. SHC Guma and SHC Manabasti -> PHC Dondekala (client roster + Mitanin list agree) ---
    # SHC Nakti is intentionally left out until its parent is confirmed.
    """UPDATE health_facilities SET parent_facility_id = '82c15be1-fa26-4629-941d-73ccc41a0c6e'
       WHERE facility_type = 'SHC' AND parent_facility_id IS DISTINCT FROM '82c15be1-fa26-4629-941d-73ccc41a0c6e'
         AND id IN ('fa826027-7aef-42d7-ad9e-41afb3e3641b',
                    '06741879-1abc-4539-bb82-76ffd0d9c0eb');""",
]

CHECKS = [
    ("villages by block", "SELECT block, count(*) FROM villages GROUP BY 1 ORDER BY 1"),
    ("PHCs (two blocks) with no CHC parent",
     "SELECT name FROM health_facilities WHERE facility_type='PHC' AND block IN ('Abhanpur','Dharsiwa') AND parent_facility_id IS NULL"),
    ("SHCs (two blocks) with no parent",
     "SELECT name FROM health_facilities WHERE facility_type='SHC' AND block IN ('Abhanpur','Dharsiwa') AND parent_facility_id IS NULL"),
    ("villages whose block differs from their SHC's block",
     "SELECT v.name, v.block, f.block FROM villages v JOIN health_facilities f ON f.id = v.shc_id WHERE v.block <> f.block"),
]


async def migrate():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            res = await conn.execute(text(stmt))
            print(f"{res.rowcount:>4} rows | {' '.join(stmt.split())[:90]}")
        print("\n--- checks ---")
        for label, sql in CHECKS:
            rows = (await conn.execute(text(sql))).fetchall()
            print(f"{label}: {len(rows)}")
            for r in rows:
                print("   ", tuple(r))
    print("\n✅ Done")


if __name__ == "__main__":
    asyncio.run(migrate())