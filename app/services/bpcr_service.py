from uuid import UUID

from sqlalchemy import desc, func,or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    ANCVisit, Beneficiary, BPCRAnswer, BPCRBloodDonor, BPCRSelectedFacility,
    FieldWorker, HealthFacility, HealthWorker, PregnancyRegistration, User,
    Village, WorkerStatus,
)
from app.schemas.women import ANC_VISIT_TEMPLATE
from app.services.bpcr_rules import (
    birth_companion_done, build_result, delivery_bag_done, financial_done,
    transport_options_complete,
)

# The 2 pilot blocks. Facility master stores these title-case in sub_district.
BPCR_SUB_DISTRICTS = ("abhanpur", "dharsiwa")
BPCR_FACILITY_TYPES = ("CHC", "PHC", "SHC", "SDH")
ROLE_LABELS = {"CHO": "CHO", "RHO_FEMALE": "RHO (Female)", "RHO_MALE": "RHO (Male)", "ANM_2ND": "ANM"}


def is_selectable(f: HealthFacility) -> bool:
    return f.facility_type in BPCR_FACILITY_TYPES and (f.sub_district or "").lower() in BPCR_SUB_DISTRICTS


def facility_out(f: HealthFacility, selected_ids: set, catchment_ids: set) -> dict:
    # Coordinates are deliberately NOT returned: they are sub-district placeholders.
    return {
        "id": str(f.id),
        "name": f.name,
        "facility_type": f.facility_type,
        "sub_district": f.sub_district,
        "is_24x7": f.is_24x7,
        "is_fru": f.is_fru,
        "category": f.category,
        "is_selected": f.id in selected_ids,
        "in_catchment": f.id in catchment_ids,
    }


async def get_catchment(b: Beneficiary, db: AsyncSession) -> list:
    """Her village's SHC, then its PHC, then its CHC (whichever exist). Empty if village not listed."""
    if not b.village_id:
        return []
    shc_id = (await db.execute(select(Village.shc_id).where(Village.id == b.village_id))).scalar_one_or_none()
    chain, seen, fid = [], set(), shc_id
    while fid and fid not in seen and len(chain) < 3:
        seen.add(fid)
        f = (await db.execute(select(HealthFacility).where(HealthFacility.id == fid))).scalar_one_or_none()
        if not f:
            break
        chain.append(f)
        fid = f.parent_facility_id
    return chain


async def search_facilities(q: str, limit: int, db: AsyncSession) -> list:
    stmt = select(HealthFacility).where(
        func.lower(HealthFacility.sub_district).in_(BPCR_SUB_DISTRICTS),
        HealthFacility.facility_type.in_(BPCR_FACILITY_TYPES),
    )
    q = (q or "").strip()
    if q:
        sim = func.similarity(HealthFacility.name, q)
        substr = HealthFacility.name.ilike(f"%{q}%")
        stmt = stmt.where(or_(substr, sim > 0.25)).order_by(desc(substr), desc(sim), HealthFacility.name)
    else:
        stmt = stmt.order_by(HealthFacility.facility_type, HealthFacility.name)
    return list((await db.execute(stmt.limit(limit))).scalars().all())


async def get_selected_facilities(beneficiary_id: UUID, db: AsyncSession) -> list:
    res = await db.execute(
        select(HealthFacility)
        .join(BPCRSelectedFacility, BPCRSelectedFacility.facility_id == HealthFacility.id)
        .where(BPCRSelectedFacility.beneficiary_id == beneficiary_id)
        .order_by(HealthFacility.facility_type, HealthFacility.name)
    )
    return list(res.scalars().all())


async def _children(parent_ids: list, db: AsyncSession) -> list:
    if not parent_ids:
        return []
    res = await db.execute(select(HealthFacility).where(HealthFacility.parent_facility_id.in_(parent_ids)))
    return list(res.scalars().all())


async def get_asha_contact(b: Beneficiary, db: AsyncSession):
    """Explicit query, not b.asha_worker: lazy-loading relationships fails in async sessions."""
    if not b.asha_id:
        return None
    row = (await db.execute(
        select(User.name, User.mobile)
        .join(FieldWorker, FieldWorker.user_id == User.id)
        .where(FieldWorker.id == b.asha_id)
    )).first()
    return {"name": row.name, "mobile": row.mobile} if row else None


async def sba_for_selected(b: Beneficiary, selected: list, db: AsyncSession) -> list:
    """
    Staff (CHO / RHO / ANM = all SBA) for each selected facility.
    Staff are attached to SHCs only. For a selected PHC or CHC we report
    no_direct_sba_data=True and list the staff of the SHCs under it.
    """
    own_shc_id = None
    if b.village_id:
        own_shc_id = (await db.execute(select(Village.shc_id).where(Village.id == b.village_id))).scalar_one_or_none()

    out = []
    for fac in selected:
        if fac.facility_type == "SHC":
            shcs, via_child = [fac], False
        elif fac.facility_type == "PHC":
            shcs, via_child = await _children([fac.id], db), True
        elif fac.facility_type == "CHC":
            phcs = await _children([fac.id], db)
            shcs, via_child = await _children([p.id for p in phcs], db), True
        else:
            shcs, via_child = [], False
        shcs = [s for s in shcs if s.facility_type == "SHC"]

        by_shc = {}
        if shcs:
            res = await db.execute(
                select(HealthWorker).where(
                    HealthWorker.facility_id.in_([s.id for s in shcs]),
                    HealthWorker.status.in_([WorkerStatus.active, WorkerStatus.data_issue]),
                )
            )
            for w in res.scalars().all():
                by_shc.setdefault(w.facility_id, []).append(w)

        groups = []
        for s in shcs:
            workers = by_shc.get(s.id, [])
            if not workers:
                continue
            groups.append({
                "shc_id": str(s.id),
                "shc_name": s.name,
                "is_own_village_shc": s.id == own_shc_id,
                "staff": [{
                    "role": w.role.value,
                    "role_label": ROLE_LABELS.get(w.role.value, w.role.value),
                    "name": w.name,
                    # data_issue rows have a name but an unreliable number: no call button
                    "mobile": w.mobile if w.status == WorkerStatus.active else None,
                } for w in workers],
            })
        groups.sort(key=lambda g: (not g["is_own_village_shc"], g["shc_name"]))

        out.append({
            "facility": {"id": str(fac.id), "name": fac.name, "facility_type": fac.facility_type},
            "no_direct_sba_data": fac.facility_type != "SHC",
            "staff_via_child_facilities": via_child,
            "groups": groups,
        })
    return out


async def get_answers(beneficiary_id: UUID, db: AsyncSession) -> dict:
    res = await db.execute(select(BPCRAnswer).where(BPCRAnswer.beneficiary_id == beneficiary_id))
    return {a.component: (a.answers or {}) for a in res.scalars().all()}


async def compute_score(b: Beneficiary, db: AsyncSession) -> dict:
    reg = (await db.execute(
        select(PregnancyRegistration.is_registered).where(PregnancyRegistration.beneficiary_id == b.id)
    )).scalar_one_or_none()

    visits = (await db.execute(
        select(ANCVisit).where(ANCVisit.beneficiary_id == b.id).order_by(desc(ANCVisit.visit_date))
    )).scalars().all()
    latest = {}
    for v in visits:
        if v.visit_number and v.visit_number not in latest:
            latest[v.visit_number] = v

    def visit_done(n: int) -> bool:
        v = latest.get(n)
        checklist = (v.checklist or {}) if v else {}
        return all(checklist.get(key) for key, _hi, _en in ANC_VISIT_TEMPLATE[n]["items"])

    selected = await get_selected_facilities(b.id, db)
    sba = await sba_for_selected(b, selected, db)
    answers = await get_answers(b.id, db)
    donors = (await db.execute(
        select(func.count(BPCRBloodDonor.id)).where(BPCRBloodDonor.beneficiary_id == b.id)
    )).scalar() or 0

    transport = answers.get("transport", {})
    options = transport_options_complete(transport)
    flags = {
        "pregnancy_registration": bool(reg),
        "anc_completion": all(visit_done(n) for n in ANC_VISIT_TEMPLATE),
        "facility_identified": len(selected) > 0,
        "sba_identified": any(g["staff"] for item in sba for g in item["groups"]),
        "transport_plan": options >= 1,
        "backup_transport": options >= 2,
        "birth_companion": birth_companion_done(transport),
        "financial_preparedness": financial_done(
            answers.get("saved_money"), answers.get("community_financial_support")),
        "blood_group_known": bool((b.blood_group or "").strip()),
        "blood_donor_identified": donors > 0,
        "delivery_bag": delivery_bag_done(answers.get("delivery_bag")),
    }
    return build_result(flags)