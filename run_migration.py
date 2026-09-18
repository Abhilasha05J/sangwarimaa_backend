# use this file for db chnages and 
import asyncio
from app.core.database import engine

STATEMENTS = [
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS husband_contact_no VARCHAR(15);",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS other_family_member_name TEXT;",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS other_family_member_relation VARCHAR(50);",
    "ALTER TABLE beneficiaries ADD COLUMN IF NOT EXISTS family_contact_no VARCHAR(15);",
]

async def migrate():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))
    print("✅ Columns added!")

if __name__ == "__main__":
    from sqlalchemy import text
    asyncio.run(migrate())