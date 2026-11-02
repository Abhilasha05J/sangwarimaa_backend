"""
app/services/asha_service.py
ASHA (Mitanin) directory: picker options, selection, contact lookup, bilingual village search.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models.models import Beneficiary, HealthFacility, MitaninDirectory, Village


def _pick(lang: str, hi: Optional[str], en: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    """Name in the app language; falls back to the other language, then to `fallback`."""
    first, second = (hi, en) if lang == "hi" else (en, hi)
    return first or second or fallback


def mask_mobile(mobile: Optional[str]) -> Optional[str]:
    return "******" + mobile[-4:] if mobile else None


def _block_filter(column, block: Optional[str]):
    """Block spelling differs between tables/versions (Dharsiwa / DHARSEEVA): compare the first 5 letters."""
    return func.lower(func.left(column, 5)) == block.lower()[:5]


def _base():
    return (
        select(
            MitaninDirectory,
            Village.name_hi, Village.name_en, Village.name,
            HealthFacility.name_hi, HealthFacility.name_en, HealthFacility.name,
        )
        .join(Village, Village.id == MitaninDirectory.village_id, isouter=True)
        .join(HealthFacility, HealthFacility.id == MitaninDirectory.shc_id, isouter=True)
        .where(MitaninDirectory.is_active.is_(True))
    )


def _out(row, lang: str, full_mobile: bool = False) -> dict:
    m, v_hi, v_en, v_name, s_hi, s_en, s_name = row
    return {
        "id": str(m.id),
        "name": m.name,
        "father_husband_name": m.father_husband_name,
        "village": _pick(lang, v_hi, v_en, v_name),
        "shc": _pick(lang, s_hi, s_en, s_name),
        "block": m.block,
        "mobile": m.mobile if full_mobile else mask_mobile(m.mobile),
    }


async def asha_options(b: Beneficiary, q: str, lang: str, limit: int, db: AsyncSession) -> dict:
    """Her village's ASHAs first, then the rest of her SHC, then a name search inside her block."""
    shc_id, block = None, b.block
    if b.village_id:
        v = (await db.execute(select(Village).where(Village.id == b.village_id))).scalar_one_or_none()
        if v:
            shc_id, block = v.shc_id, v.block or b.block

    out = {"selected_id": str(b.mitanin_id) if b.mitanin_id else None, "village": [], "shc": [], "results": []}
    seen = set()

    def add(key, rows):
        for r in rows:
            if r[0].id in seen:
                continue
            seen.add(r[0].id)
            out[key].append(_out(r, lang))

    if b.village_id:
        rows = (await db.execute(
            _base().where(MitaninDirectory.village_id == b.village_id).order_by(MitaninDirectory.name)
        )).all()
        add("village", rows)
    if shc_id:
        rows = (await db.execute(
            _base().where(MitaninDirectory.shc_id == shc_id).order_by(MitaninDirectory.name)
        )).all()
        add("shc", rows)

    qn = q.strip()
    if qn:
        like = f"%{qn}%"
        sim = func.greatest(
            func.similarity(MitaninDirectory.name, qn),
            func.similarity(func.coalesce(MitaninDirectory.father_husband_name, ""), qn),
        )
        stmt = _base().where(
            or_(MitaninDirectory.name.ilike(like), MitaninDirectory.father_husband_name.ilike(like), sim > 0.3)
        )
        if block:
            stmt = stmt.where(_block_filter(MitaninDirectory.block, block))
        rows = (await db.execute(stmt.order_by(desc(sim), MitaninDirectory.name).limit(limit))).all()
        add("results", rows)
    return out


async def select_asha(b: Beneficiary, mitanin_id: Optional[UUID], db: AsyncSession) -> None:
    """Set (or clear, with None) her ASHA. asha_id follows the directory row's real login, if it has one."""
    if mitanin_id is None:
        b.mitanin_id = None
        b.asha_id = None
    else:
        m = (await db.execute(
            select(MitaninDirectory).where(MitaninDirectory.id == mitanin_id, MitaninDirectory.is_active.is_(True))
        )).scalar_one_or_none()
        if not m:
            raise NotFoundException("ASHA")
        b.mitanin_id = m.id
        b.asha_id = m.field_worker_id
    await db.commit()
    await db.refresh(b)


async def asha_contact_for(b: Beneficiary, db: AsyncSession) -> Optional[dict]:
    """{'name','mobile','source'} for her ASHA, whichever table it lives in; None if she has none."""
    if b.mitanin_id:
        m = (await db.execute(select(MitaninDirectory).where(MitaninDirectory.id == b.mitanin_id))).scalar_one_or_none()
        if m:
            return {"name": m.name, "mobile": m.mobile, "source": "directory", "mitanin_id": str(m.id)}
    if b.asha_id and getattr(b, "asha_worker", None) and b.asha_worker.user:
        u = b.asha_worker.user
        return {"name": u.name, "mobile": u.mobile, "source": "app", "mitanin_id": None}
    return None


async def current_asha(b: Beneficiary, lang: str, db: AsyncSession) -> dict:
    if b.mitanin_id:
        row = (await db.execute(_base().where(MitaninDirectory.id == b.mitanin_id))).first()
        if row:
            return {"selected": True, "source": "directory", "asha": _out(row, lang, full_mobile=True)}
    contact = await asha_contact_for(b, db)
    if contact:
        return {"selected": True, "source": contact["source"],
                "asha": {"id": contact["mitanin_id"], "name": contact["name"], "mobile": contact["mobile"]}}
    return {"selected": False, "source": None, "asha": None}


async def search_villages_bilingual(q: str, block: Optional[str], lang: str, limit: int, db: AsyncSession) -> list:
    qn = q.strip()
    stmt = select(Village, HealthFacility.name_hi, HealthFacility.name_en, HealthFacility.name.label("shc_name")).join(
        HealthFacility, Village.shc_id == HealthFacility.id, isouter=True
    )
    if qn:
        like = f"%{qn}%"
        sim = func.greatest(
            func.similarity(func.coalesce(Village.name_en, Village.name), qn),
            func.similarity(func.coalesce(Village.name_hi, ""), qn),
        )
        is_sub = or_(Village.name.ilike(like), Village.name_en.ilike(like), Village.name_hi.ilike(like))
        stmt = stmt.where(or_(is_sub, sim > 0.25)).order_by(desc(is_sub), desc(sim), Village.name)
    else:
        stmt = stmt.order_by(func.coalesce(Village.name_hi if lang == "hi" else Village.name_en, Village.name))
    if block:
        stmt = stmt.where(_block_filter(Village.block, block))
    rows = (await db.execute(stmt.limit(limit))).all()
    return [
        {
            "id": str(v.id),
            "name": _pick(lang, v.name_hi, v.name_en, v.name),
            "name_hi": v.name_hi,
            "name_en": v.name_en or v.name,
            "code": v.code,
            "block": v.block,
            "shc_name": _pick(lang, s_hi, s_en, s_name),
            "has_facility_data": v.shc_id is not None,
        }
        for v, s_hi, s_en, s_name in rows
    ]
