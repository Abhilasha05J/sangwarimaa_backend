"""
Read-only DB status check for Sangwari Maa (ASHA seeding prep).

Usage:
    pip install psycopg2-binary
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    python db_status_check.py

Outputs:
    - console report
    - ./db_status/report.txt
    - ./db_status/health_facilities.csv, villages.csv, health_workers.csv, field_workers.csv
      (no users / beneficiaries rows are exported, only counts)

Safe: the session is set to READ ONLY, nothing is written.
"""
import csv
import os
import sys

import psycopg2

OUT = "db_status"
os.makedirs(OUT, exist_ok=True)
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


url = os.environ.get("DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
if not url:
    sys.exit("Set DATABASE_URL or pass it as the first argument")
url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")

# asyncpg-style params (ssl=require) -> psycopg2 style (sslmode=require)
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_p = urlsplit(url)
_qs = []
for k, v in parse_qsl(_p.query, keep_blank_values=True):
    if k == "ssl":
        k = "sslmode"
        v = {"true": "require", "1": "require", "false": "disable", "0": "disable"}.get(v.lower(), v)
    _qs.append((k, v))
url = urlunsplit(_p._replace(query=urlencode(_qs)))

conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()


def q(sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def cols(table):
    return {r[0]: r[1] for r in q(
        "select column_name, data_type from information_schema.columns "
        "where table_schema='public' and table_name=%s order by ordinal_position", (table,))}


def exists(table):
    return bool(cols(table))


def breakdown(table, col, limit=60):
    rows = q(f'select "{col}", count(*) from "{table}" group by 1 order by 2 desc nulls last limit {limit}')
    log(f"  by {col}:")
    for v, n in rows:
        log(f"    {v!r:45} {n}")


def null_count(table, col):
    n = q(f'select count(*) from "{table}" where "{col}" is null')[0][0]
    log(f"  {col} is NULL: {n}")


def export(table):
    cur.execute(f'select * from "{table}"')
    with open(f"{OUT}/{table}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur.fetchall())


log("=== Server ===")
log(q("select version()")[0][0])
ext = [r[0] for r in q("select extname from pg_extension")]
log(f"extensions: {ext}  (pg_trgm present: {'pg_trgm' in ext})")
if exists("alembic_version"):
    log(f"alembic version: {q('select version_num from alembic_version')}")

log("\n=== All tables (row counts) ===")
for (t,) in q("select table_name from information_schema.tables "
              "where table_schema='public' and table_type='BASE TABLE' order by 1"):
    n = q('select count(*) from "%s"' % t)[0][0]
    log(f"  {t:35} {n}")

# ---------------------------------------------------------------- health_facilities
log("\n=== health_facilities ===")
if exists("health_facilities"):
    c = cols("health_facilities")
    log(f"  columns: {list(c)}")
    log(f"  total: {q('select count(*) from health_facilities')[0][0]}")
    for col in c:
        if col in ("facility_type", "type", "level", "block", "district", "block_name",
                   "sub_district", "category", "is_active", "match_status", "source"):
            breakdown("health_facilities", col)
    if "parent_facility_id" in c:
        null_count("health_facilities", "parent_facility_id")
    export("health_facilities")
else:
    log("  table not found")

# ---------------------------------------------------------------- villages
log("\n=== villages ===")
if exists("villages"):
    c = cols("villages")
    log(f"  columns: {list(c)}")
    log(f"  total: {q('select count(*) from villages')[0][0]}")
    for col in ("block", "district", "match_status", "is_active"):
        if col in c:
            breakdown("villages", col)
    for col in ("shc_id", "code"):
        if col in c:
            null_count("villages", col)
    if "code" in c:
        d = q('select count(*) from (select code from villages where code is not null '
              'group by code having count(*)>1) x')[0][0]
        log(f"  duplicate codes: {d}")
    if {"shc_id", "block"} <= set(c) and exists("health_facilities"):
        log("  villages per SHC (top 10):")
        for r in q("select f.name, count(*) from villages v join health_facilities f on f.id=v.shc_id "
                   "group by 1 order by 2 desc limit 10"):
            log(f"    {r[0]!r:45} {r[1]}")
    export("villages")
else:
    log("  table not found")

# ---------------------------------------------------------------- health_workers
log("\n=== health_workers (CHO/RHO/2nd ANM roster) ===")
if exists("health_workers"):
    c = cols("health_workers")
    log(f"  columns: {list(c)}")
    log(f"  total: {q('select count(*) from health_workers')[0][0]}")
    for col in ("role", "worker_role", "block"):
        if col in c:
            breakdown("health_workers", col)
    export("health_workers")
else:
    log("  table not found")

# ---------------------------------------------------------------- field_workers
log("\n=== field_workers (ASHA/ANM, login-capable) ===")
if exists("field_workers"):
    c = cols("field_workers")
    log(f"  columns: {list(c)}")
    log(f"  total: {q('select count(*) from field_workers')[0][0]}")
    for col in ("worker_role", "block", "district"):
        if col in c:
            breakdown("field_workers", col)
    if "user_id" in c:
        null_count("field_workers", "user_id")
    export("field_workers")
else:
    log("  table not found")

# ---------------------------------------------------------------- users / beneficiaries (counts only)
log("\n=== users (counts only) ===")
if exists("users"):
    c = cols("users")
    log(f"  total: {q('select count(*) from users')[0][0]}")
    for col in ("role",):
        if col in c:
            breakdown("users", col)

log("\n=== beneficiaries (counts only) ===")
for t in ("beneficiaries", "beneficiary"):
    if exists(t):
        c = cols(t)
        log(f"  table: {t}, total: {q(f'select count(*) from {t}')[0][0]}")
        for col in ("asha_id", "anm_id", "village_id", "shc_id", "facility_id"):
            if col in c:
                n = q(f'select count(*) from {t} where {col} is not null')[0][0]
                log(f"  {col} populated: {n}")
        break

with open(f"{OUT}/report.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
log(f"\nDone. Files in ./{OUT}/ (send report.txt and the CSVs back to compare with the ASHA mapping).")