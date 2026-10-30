#!/usr/bin/env python3
"""
Sangwari Maa - seed bilingual names, PHC/CHC links, villages and the Mitanin (ASHA) directory.

Run 001_asha_directory.sql first.

    pip install psycopg2-binary pandas openpyxl
    export DATABASE_URL="postgresql://user:pass@host:5432/db?sslmode=require"

    # dry run (default): does everything in one transaction, prints counts, ROLLS BACK
    python seed_asha.py --mitanin Mitanin_list_of_Raipur_District.xlsx

    # real run
    python seed_asha.py --mitanin Mitanin_list_of_Raipur_District.xlsx --apply

Safe to re-run: every step is an upsert. Nothing is deleted. No users / field_workers are created.
Reports are written to ./seed_reports/.
"""
import argparse
import os
import re
import sys
import unicodedata
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import psycopg2
import psycopg2.extras

BLOCK_DB = {"Abhanpur": "ABHANPUR", "Dharsiwa": "DHARSEEVA"}  # villages.block spelling
SHC_PREFIX = re.compile(r"^.*?(?:केन्द्र|केंद्र)\s*")


# ───────────────────────────── helpers ─────────────────────────────
def fix_dsn(url: str) -> str:
    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    p = urlsplit(url)
    qs = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if k == "ssl":
            k, v = "sslmode", {"true": "require", "1": "require", "false": "disable", "0": "disable"}.get(v.lower(), v)
        qs.append((k, v))
    return urlunsplit(p._replace(query=urlencode(qs)))


def sv(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT else str(x).strip()


def norm_hi(s: str) -> str:
    """NFC (makes ड़ consistent), drop zero-width joiners, collapse spaces."""
    s = unicodedata.normalize("NFC", sv(s)).replace("\u200d", "").replace("\u200c", "")
    return re.sub(r"\s+", " ", s).strip()


def strip_suffix(s: str) -> str:
    return re.sub(r"\s*[-–]\s*\d+$", "", s).strip()


def tidy_case(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" .,-")
    return s.title() if (s.isupper() or s.islower()) else s


def clean_name(raw: str) -> str:
    s = re.sub(r"\s+", " ", sv(raw)).strip()
    s = re.sub(r"^(mrs|smt|miss|ku|kum|shrimati)\.?\s+", "", s, flags=re.I)
    if s.isupper():                       # "YADAVWO SHRI RAM" -> "YADAV WO SHRI RAM"
        s = re.sub(r"(?<=[A-Z])WO(?=\s+[A-Z])", " WO", s)
    s = re.split(r"\s+(?:w/?o|d/?o|c/?o)\b", s, flags=re.I)[0]
    return tidy_case(s)


def norm_blood(x):
    s = sv(x)
    if not s or s.lower() == "select":
        return None
    return s.replace("ve", "").replace("VE", "")


def log(msg=""):
    print(msg)
    REPORT.append(str(msg))


REPORT = []


# ───────────────────────────── load inputs ─────────────────────────────
def load_mitanin(path: str) -> pd.DataFrame:
    frames = []
    for sheet in ("Abhanpur", "Dharsiwa"):
        d = pd.read_excel(path, sheet_name=sheet)
        d = d.iloc[:, :12].copy()
        d.columns = ["sn", "name_raw", "spouse", "dob", "mobile", "block", "chc", "phc", "shc", "village", "bg", "note"]
        d = d[pd.to_numeric(d["sn"], errors="coerce").notna() & d["mobile"].notna()].copy()  # drops footer rows
        d["block"] = sheet
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["mobile"] = df["mobile"].map(lambda x: str(int(float(x))))
    bad = df[~df["mobile"].str.fullmatch(r"\d{10}")]
    if len(bad):
        log(f"WARNING: {len(bad)} rows with a mobile that is not 10 digits (skipped): {bad['mobile'].tolist()[:5]}")
        df = df[df["mobile"].str.fullmatch(r"\d{10}")]
    dup = df[df["mobile"].duplicated(keep=False)]
    if len(dup):
        log(f"WARNING: {len(dup)} rows share a mobile number; the last one wins: {dup['mobile'].unique().tolist()[:5]}")
        df = df.drop_duplicates("mobile", keep="last")
    return df


# ───────────────────────────── main ─────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mitanin", required=True, help="Mitanin_list_of_Raipur_District.xlsx")
    ap.add_argument("--villages", default="Villages_Final_for_Seed.xlsx")
    ap.add_argument("--data-dir", default="seed_data")
    ap.add_argument("--report-dir", default="seed_reports")
    ap.add_argument("--apply", action="store_true", help="commit (default is a dry run that rolls back)")
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    os.makedirs(a.report_dir, exist_ok=True)

    shc_hi = pd.read_csv(os.path.join(a.data_dir, "shc_hindi_map.csv"), dtype=str)
    names = pd.read_csv(os.path.join(a.data_dir, "facility_names.csv"), dtype=str, keep_default_na=False)
    parents = pd.read_csv(os.path.join(a.data_dir, "shc_parent_links.csv"), dtype=str, keep_default_na=False)
    vil = pd.read_excel(a.villages, sheet_name="Final_villages", dtype=str, keep_default_na=False)
    mit = load_mitanin(a.mitanin)
    log(f"Mitanin rows read: {len(mit)} (Abhanpur {int((mit.block=='Abhanpur').sum())}, Dharsiwa {int((mit.block=='Dharsiwa').sum())})")

    conn = psycopg2.connect(fix_dsn(url))
    conn.autocommit = False
    cur = conn.cursor()

    # pre-checks: migration applied?
    cur.execute("select count(*) from information_schema.columns where table_name='villages' and column_name in ('name_hi','name_en')")
    if cur.fetchone()[0] != 2:
        sys.exit("Run 001_asha_directory.sql first (villages.name_hi/name_en missing)")
    cur.execute("select to_regclass('public.mitanin_directory')")
    if cur.fetchone()[0] is None:
        sys.exit("Run 001_asha_directory.sql first (mitanin_directory missing)")

    try:
        # ── 1. facility names ─────────────────────────────────────────────
        rows = [(r.name_hi or None, r.name_en or None, r.facility_id) for r in names.itertuples()]
        psycopg2.extras.execute_values(
            cur,
            "update health_facilities h set name_hi = v.hi, name_en = v.en "
            "from (values %s) as v(hi, en, id) where h.id = v.id::uuid",
            rows,
        )
        log(f"[1] facility names set: {len(rows)} facilities")

        # ── 2. PHC / CHC parent links ─────────────────────────────────────
        todo = parents[parents.status.isin(["fill", "fix"])]
        skipped = parents[parents.status == "pending_client"]
        psycopg2.extras.execute_values(
            cur,
            "update health_facilities h set parent_facility_id = v.pid::uuid "
            "from (values %s) as v(id, pid) where h.id = v.id::uuid",
            [(r.shc_id, r.parent_id) for r in todo.itertuples()],
        )
        log(f"[2] parent links set: {len(todo)}  | skipped, waiting for client: {skipped.shc_name.tolist()}")

        # ── 3. villages: update existing, insert new ──────────────────────
        upd = ins = 0
        for r in vil.itertuples():
            hi, en = r.name_hi or None, r.name_en or None
            fid = r.facility_id or None
            if r.village_id:
                cur.execute(
                    "update villages set name_hi=%s, name_en=%s, shc_id=coalesce(%s::uuid, shc_id), updated_at=now() where id=%s",
                    (hi, en, fid, r.village_id),
                )
                upd += cur.rowcount
            else:
                block = BLOCK_DB[r.block]
                cur.execute(
                    "select id from villages where source='mitanin_list' and block=%s and shc_id is not distinct from %s::uuid "
                    "and name_hi is not distinct from %s and name_en is not distinct from %s",
                    (block, fid, hi, en),
                )
                hit = cur.fetchone()
                if hit:
                    continue  # already inserted by an earlier run
                cur.execute(
                    "insert into villages (id, name, code, block, district, shc_id, match_status, source, created_at, updated_at, name_hi, name_en) "
                    "values (%s, %s, null, %s, 'Raipur', %s::uuid, 'exact', 'mitanin_list', now(), now(), %s, %s)",
                    (str(uuid.uuid4()), en or hi, block, fid, hi, en),
                )
                ins += 1
        log(f"[3] villages updated: {upd}, inserted: {ins}")

        # ── 4. Mitanin directory ──────────────────────────────────────────
        shc_lookup = {(r.block, norm_hi(r.shc_hindi)): r.facility_id for r in shc_hi.itertuples()}
        cur.execute("select id::text, block, shc_id::text, name_hi from villages where name_hi is not null")
        vmap = {}  # (block_db, normalized hindi) -> [(village_id, shc_id)]
        for vid, blk, sid, nh in cur.fetchall():
            for key in {norm_hi(nh), strip_suffix(norm_hi(nh))}:
                vmap.setdefault((blk, key), []).append((vid, sid))

        out = []
        for r in mit.itertuples():
            shc_txt, vil_txt = sv(r.shc), sv(r.village)
            shc_id = village_id = None
            status, note = "unresolved", []
            if shc_txt and shc_txt != "-":
                shc_id = shc_lookup.get((r.block, norm_hi(SHC_PREFIX.sub("", shc_txt))))
                if not shc_id:
                    note.append("SHC name not in mapping")
            else:
                note.append("no SHC in client list")
            if vil_txt and vil_txt != "-":
                key = norm_hi(vil_txt)
                cands = vmap.get((BLOCK_DB[r.block], key)) or vmap.get((BLOCK_DB[r.block], strip_suffix(key))) or []
                same = [c for c in cands if shc_id and c[1] == shc_id]
                if len({c[0] for c in same}) == 1:
                    village_id = same[0][0]
                elif len({c[0] for c in cands}) == 1:
                    village_id = cands[0][0]
                    note.append("village found under a different SHC")
                elif cands:
                    note.append("village name ambiguous")
                else:
                    note.append("village not found")
            else:
                note.append("no village in client list")
            if shc_id and village_id and "village found under a different SHC" not in note:
                status = "exact"
            elif shc_id:
                status = "likely"
            flag = sv(r.note).lower()
            active = not any(w in flag for w in ("death", "no working"))
            if not active:
                note.append(f"client note: {sv(r.note)}")
            out.append(
                (
                    clean_name(r.name_raw), re.sub(r"\s+", " ", sv(r.name_raw)), tidy_case(sv(r.spouse)) or None,
                    None if pd.isna(r.dob) else pd.Timestamp(r.dob).date(), r.mobile, norm_blood(r.bg), r.block,
                    shc_id, village_id, shc_txt or None, vil_txt or None, sv(r.phc) or None,
                    status, active, "; ".join(note) or None,
                )
            )
        psycopg2.extras.execute_values(
            cur,
            "insert into mitanin_directory (name, name_raw, father_husband_name, dob, mobile, blood_group, block, "
            "shc_id, village_id, shc_text_raw, village_text_raw, phc_text_raw, match_status, is_active, note) values %s "
            "on conflict (mobile) do update set name=excluded.name, name_raw=excluded.name_raw, "
            "father_husband_name=excluded.father_husband_name, dob=excluded.dob, blood_group=excluded.blood_group, "
            "block=excluded.block, shc_id=excluded.shc_id, village_id=excluded.village_id, "
            "shc_text_raw=excluded.shc_text_raw, village_text_raw=excluded.village_text_raw, phc_text_raw=excluded.phc_text_raw, "
            "match_status=excluded.match_status, is_active=excluded.is_active, note=excluded.note, updated_at=now()",
            out,
            template="(%s,%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s)",
        )
        cur.execute("select match_status, count(*) from mitanin_directory group by 1 order by 1")
        log(f"[4] mitanin_directory rows written: {len(out)} | by status: {dict(cur.fetchall())}")
        cur.execute("select count(*) from mitanin_directory where is_active = false")
        log(f"    inactive: {cur.fetchone()[0]}")

        # ── 5. report: everything not 'exact' ─────────────────────────────
        cur.execute(
            "select m.mobile, m.name, m.block, m.match_status, m.shc_text_raw, m.village_text_raw, m.note, f.name "
            "from mitanin_directory m left join health_facilities f on f.id = m.shc_id "
            "where m.match_status <> 'exact' or not m.is_active order by m.block, m.match_status, m.name"
        )
        rep = pd.DataFrame(cur.fetchall(), columns=["mobile", "name", "block", "status", "shc_text", "village_text", "note", "shc_in_db"])
        rep.to_csv(os.path.join(a.report_dir, "mitanin_review.csv"), index=False, encoding="utf-8-sig")
        log(f"[5] review file: {len(rep)} rows -> {a.report_dir}/mitanin_review.csv")

        cur.execute("select count(*) from health_facilities where facility_type='SHC' and block in ('Abhanpur','Dharsiwa') and parent_facility_id is null")
        log(f"    SHCs in the two blocks still without a PHC/CHC parent: {cur.fetchone()[0]}")
        cur.execute("select count(*) from villages where shc_id is null")
        log(f"    villages still without an SHC: {cur.fetchone()[0]}")

        if a.apply:
            conn.commit()
            log("\nCOMMITTED.")
        else:
            conn.rollback()
            log("\nDRY RUN: rolled back. Re-run with --apply to save.")
    except Exception:
        conn.rollback()
        raise
    finally:
        with open(os.path.join(a.report_dir, "seed_summary.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(REPORT))
        conn.close()


if __name__ == "__main__":
    main()