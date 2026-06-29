"""
Async seed script: loads faq_seed.json into the new `faqs` table.

Usage:
    python seed_faqs.py

TODO before running:
- Adjust the AsyncSessionLocal import to match your actual async session
  factory in app/core/database.py.
- Adjust the FAQ import path to wherever you put faq_model.py.

IMPORTANT: title_hi / content_hi are null for every row — the source FAQ
docs (BPCR, MHS, PNC) only contained English content. Hindi translations
need to come from the client or a professional medical translator (not
machine translation, given this is health guidance) and be backfilled via
an UPDATE. Until then, the frontend's FaqItem.title()/content() helpers
already fall back to English when the Hindi field is empty.
"""
import asyncio
import json
import uuid

from app.core.database import AsyncSessionLocal  # TODO: adjust to your actual name
from app.models.models import FAQ  # TODO: adjust import path

SEED_PATH = "faq_seed.json"


async def main():
    with open(SEED_PATH, encoding="utf-8") as f:
        entries = json.load(f)

    async with AsyncSessionLocal() as session:
        for entry in entries:
            session.add(FAQ(
                id=uuid.uuid4(),
                category=entry["category"],
                subcategory=entry["subcategory"],
                title_hi=entry["title_hi"],
                title_en=entry["title_en"],
                content_hi=entry["content_hi"],
                content_en=entry["content_en"],
                tags=entry["tags"],
                display_order=entry["display_order"],
                is_active=True,
            ))
        await session.commit()

    print(f"Inserted {len(entries)} FAQ rows into faqs table.")


if __name__ == "__main__":
    asyncio.run(main())