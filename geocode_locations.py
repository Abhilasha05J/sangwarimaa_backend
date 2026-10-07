# """
# One-time (re-runnable) geocoding of villages + health facilities for the 2 pilot
# blocks, with a human-review step in the middle.

#   1) python geocode_locations.py export --provider nominatim --out geocode_review.csv
#   2) Open the CSV. Check `matched` against `name`. Fix or fill lat/lng where wrong or
#      blank (Google Maps: long-press a spot -> copy the coordinates). When you replace
#      a value by hand, set `source` to `manual`.
#   3) python geocode_locations.py import --in geocode_review.csv --dry-run
#      python geocode_locations.py import --in geocode_review.csv

# Needs only `asyncpg` (your existing DB driver) and the standard library.
#   DATABASE_URL          e.g. postgresql+asyncpg://user:pass@host:5432/db
#   GEOCODER_CONTACT      your email (Nominatim's usage policy requires an identifying User-Agent)
#   GOOGLE_MAPS_API_KEY   only for --provider google (Places API (New) must be enabled)

# Villages are geocoded first. An SHC is then placed at the village with the same name
# (SHCs sit inside villages and OSM/Google rarely know them), else at the mean of the
# villages it serves, else geocoded by name. PHC/CHC/SDH are geocoded by name.
# Rows already holding coordinates are skipped unless you pass --all.
# """
# import argparse
# import asyncio
# import csv
# import json
# import os
# import re
# import sys
# import time
# import urllib.parse
# import urllib.request
# from collections import defaultdict

# import asyncpg

# PILOT_SUB_DISTRICTS = ["abhanpur", "dharsiwa"]
# FACILITY_TYPES = ["CHC", "PHC", "SHC", "SDH"]
# # Generous box around Raipur district. Anything outside is rejected.
# LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX = 20.5, 22.2, 80.7, 82.5
# TYPE_WORDS = {"CHC": "Community Health Centre", "PHC": "Primary Health Centre",
#               "SHC": "Sub Health Centre", "SDH": "Sub District Hospital"}
# BLOCK_NAMES = {"abhanpur": "Abhanpur", "dharseeva": "Dharsiwa", "dharsiwa": "Dharsiwa"}
# TABLES = {"village": "villages", "facility": "health_facilities"}
# CSV_FIELDS = ["kind", "id", "name", "block_or_type", "query", "lat", "lng", "source", "matched"]


# def user_agent() -> str:
#     return f"sangwari-maa-geocoder/1.0 ({os.getenv('GEOCODER_CONTACT', 'set GEOCODER_CONTACT')})"


# def dsn() -> str:
#     return os.environ["DATABASE_URL"].replace("+asyncpg", "")


# def in_box(lat: float, lng: float) -> bool:
#     return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


# def clean(name: str) -> str:
#     name = re.sub(r"\(.*?\)", " ", name or "")
#     return re.sub(r"\s+", " ", name).strip()


# def core_name(name: str) -> str:
#     """'SHC Tekari N' -> 'tekari'; used to match an SHC to its same-named village."""
#     s = clean(name)
#     s = re.sub(r"\b(CHC|PHC|SHC|SDH|RPR|RAIPUR)\b", " ", s, flags=re.I)
#     s = re.sub(r"\s+N$", "", s.strip(), flags=re.I)
#     return re.sub(r"[^a-z0-9]+", "", s.lower())


# def facility_query(name: str, sub_district: str) -> str:
#     s = clean(name)
#     for abbr, full in TYPE_WORDS.items():
#         s = re.sub(rf"\b{abbr}\b", full, s, flags=re.I)
#     s = re.sub(r"\b(RPR)\b", " ", s, flags=re.I)
#     s = re.sub(r"\s+N$", "", re.sub(r"\s+", " ", s).strip())
#     return f"{s}, {BLOCK_NAMES.get((sub_district or '').lower(), sub_district)}, Raipur, Chhattisgarh, India"


# def village_query(name: str, block: str) -> str:
#     return f"{clean(name)}, {BLOCK_NAMES.get((block or '').lower(), (block or '').title())}, Raipur, Chhattisgarh, India"


# # ── providers: each returns (lat, lng, matched_text) or None ─────────────────

# def _request(req: urllib.request.Request):
#     with urllib.request.urlopen(req, timeout=20) as r:
#         return json.load(r)


# def geocode_nominatim(query: str):
#     params = urllib.parse.urlencode({
#         "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in",
#         "viewbox": f"{LNG_MIN},{LAT_MAX},{LNG_MAX},{LAT_MIN}", "bounded": 1,
#     })
#     time.sleep(1.1)  # public Nominatim: max 1 request per second
#     data = _request(urllib.request.Request(
#         "https://nominatim.openstreetmap.org/search?" + params,
#         headers={"User-Agent": user_agent()}))
#     if not data:
#         return None
#     d = data[0]
#     return float(d["lat"]), float(d["lon"]), d.get("display_name", "")


# def geocode_google(query: str):
#     body = json.dumps({
#         "textQuery": query, "regionCode": "IN", "maxResultCount": 1,
#         "locationRestriction": {"rectangle": {
#             "low": {"latitude": LAT_MIN, "longitude": LNG_MIN},
#             "high": {"latitude": LAT_MAX, "longitude": LNG_MAX}}},
#     }).encode()
#     data = _request(urllib.request.Request(
#         "https://places.googleapis.com/v1/places:searchText", data=body, method="POST",
#         headers={"Content-Type": "application/json",
#                  "X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"],
#                  "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"}))
#     places = data.get("places") or []
#     if not places:
#         return None
#     p = places[0]
#     loc = p["location"]
#     return (loc["latitude"], loc["longitude"],
#             f'{(p.get("displayName") or {}).get("text", "")} - {p.get("formattedAddress", "")}')


# GEOCODERS = {"nominatim": geocode_nominatim, "google": geocode_google}


# def try_geocode(fn, query: str):
#     try:
#         res = fn(query)
#     except Exception as e:  # network / quota / bad key: keep going, row stays blank
#         print(f"  ! {query!r}: {e}", file=sys.stderr)
#         return None
#     return res if res and in_box(res[0], res[1]) else None


# # ── export ───────────────────────────────────────────────────────────────────

# async def cmd_export(args):
#     geocode = GEOCODERS[args.provider]
#     conn = await asyncpg.connect(dsn())
#     try:
#         villages = await conn.fetch(
#             "SELECT id, name, block, shc_id, geo_lat, geo_lng FROM villages ORDER BY block, name")
#         facilities = await conn.fetch(
#             """SELECT id, name, facility_type, sub_district, geo_lat, geo_lng
#                FROM health_facilities
#                WHERE lower(sub_district) = ANY($1::text[]) AND facility_type = ANY($2::text[])
#                ORDER BY sub_district, facility_type, name""",
#             PILOT_SUB_DISTRICTS, FACILITY_TYPES)
#     finally:
#         await conn.close()

#     rows, vcoord, vname = [], {}, {}
#     shc_to_villages = defaultdict(list)

#     for v in villages:
#         vname[v["id"]] = v["name"]
#         if v["shc_id"]:
#             shc_to_villages[v["shc_id"]].append(v["id"])
#         if v["geo_lat"] is not None:
#             vcoord[v["id"]] = (v["geo_lat"], v["geo_lng"])
#             if not args.all:
#                 continue
#         q = village_query(v["name"], v["block"])
#         print(f"village  {q}")
#         res = try_geocode(geocode, q)
#         if res:
#             vcoord[v["id"]] = (res[0], res[1])
#         rows.append(_row("village", v["id"], v["name"], v["block"], q, res, args.provider))

#     for f in facilities:
#         if f["geo_lat"] is not None and not args.all:
#             continue
#         q = facility_query(f["name"], f["sub_district"])
#         res, source = None, args.provider

#         if f["facility_type"] == "SHC":
#             served = [vid for vid in shc_to_villages.get(f["id"], []) if vid in vcoord]
#             same = [vid for vid in served if core_name(vname[vid]) == core_name(f["name"])]
#             if same:
#                 lat, lng = vcoord[same[0]]
#                 res, source = (lat, lng, f"village: {vname[same[0]]}"), "village_same_name"
#             elif served:
#                 lat = sum(vcoord[v][0] for v in served) / len(served)
#                 lng = sum(vcoord[v][1] for v in served) / len(served)
#                 res, source = (lat, lng, "mean of: " + ", ".join(vname[v] for v in served)), "village_centroid"

#         if res is None:
#             print(f"facility {q}")
#             res, source = try_geocode(geocode, q), args.provider
#         rows.append(_row("facility", f["id"], f["name"], f["facility_type"], q, res, source))

#     with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:  # BOM: opens cleanly in Excel
#         w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
#         w.writeheader()
#         w.writerows(rows)

#     found = sum(1 for r in rows if r["lat"] != "")
#     print(f"\nWrote {len(rows)} rows to {args.out} ({found} with coordinates, {len(rows) - found} blank).")
#     print("Review it, fix anything wrong, then run the import step.")


# def _row(kind, id_, name, block_or_type, query, res, source):
#     return {"kind": kind, "id": str(id_), "name": name, "block_or_type": block_or_type, "query": query,
#             "lat": f"{res[0]:.6f}" if res else "", "lng": f"{res[1]:.6f}" if res else "",
#             "source": source if res else "", "matched": res[2] if res else ""}


# # ── import ───────────────────────────────────────────────────────────────────

# async def cmd_import(args):
#     todo, skipped = [], 0
#     with open(args.infile, newline="", encoding="utf-8-sig") as fh:
#         for r in csv.DictReader(fh):
#             try:
#                 lat, lng = float(r["lat"]), float(r["lng"])
#             except (TypeError, ValueError):
#                 skipped += 1
#                 continue
#             if r["kind"] not in TABLES or not in_box(lat, lng):
#                 print(f"  skipped (bad kind or outside Raipur box): {r['name']} {lat},{lng}", file=sys.stderr)
#                 skipped += 1
#                 continue
#             todo.append((r["kind"], r["id"], lat, lng, (r.get("source") or "manual").strip() or "manual"))

#     print(f"{len(todo)} rows to write, {skipped} skipped.")
#     if args.dry_run:
#         print("Dry run: nothing written.")
#         return
#     conn = await asyncpg.connect(dsn())
#     try:
#         async with conn.transaction():
#             for kind, id_, lat, lng, source in todo:
#                 await conn.execute(
#                     f"UPDATE {TABLES[kind]} SET geo_lat = $1, geo_lng = $2, geo_source = $3 WHERE id = $4::uuid",
#                     lat, lng, source, id_)
#     finally:
#         await conn.close()
#     print("Done.")


# def main():
#     p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
#     sub = p.add_subparsers(dest="cmd", required=True)
#     e = sub.add_parser("export", help="geocode and write a review CSV")
#     e.add_argument("--provider", choices=list(GEOCODERS), default="nominatim")
#     e.add_argument("--out", default="geocode_review.csv")
#     e.add_argument("--all", action="store_true", help="also redo rows that already have coordinates")
#     i = sub.add_parser("import", help="load a reviewed CSV into the database")
#     i.add_argument("--in", dest="infile", required=True)
#     i.add_argument("--dry-run", action="store_true")
#     args = p.parse_args()
#     asyncio.run(cmd_export(args) if args.cmd == "export" else cmd_import(args))


# if __name__ == "__main__":
#     main()

"""
One-time (re-runnable) geocoding of villages + health facilities for the 2 pilot
blocks, with a human-review step in the middle.

  1) python geocode_locations.py export --provider nominatim --out geocode_review.csv
  2) Open the CSV. Check `matched` against `name`. Fix or fill lat/lng where wrong or
     blank (Google Maps: long-press a spot -> copy the coordinates). When you replace
     a value by hand, set `source` to `manual`.
  3) python geocode_locations.py import --in geocode_review.csv --dry-run
     python geocode_locations.py import --in geocode_review.csv

Needs only `asyncpg` (your existing DB driver) and the standard library.
  DATABASE_URL          e.g. postgresql+asyncpg://user:pass@host:5432/db
  GEOCODER_CONTACT      your email (Nominatim's usage policy requires an identifying User-Agent)
  GOOGLE_MAPS_API_KEY   only for --provider google (Places API (New) must be enabled)

Villages are geocoded first. An SHC is then placed at the village with the same name
(SHCs sit inside villages and OSM/Google rarely know them), else at the mean of the
villages it serves, else geocoded by name. PHC/CHC/SDH are geocoded by name.
Rows already holding coordinates are skipped unless you pass --all.
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

import asyncpg

PILOT_SUB_DISTRICTS = ["abhanpur", "dharsiwa"]
FACILITY_TYPES = ["CHC", "PHC", "SHC", "SDH"]
# Generous box around Raipur district. Anything outside is rejected.
LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX = 20.5, 22.2, 80.7, 82.5
TYPE_WORDS = {"CHC": "Community Health Centre", "PHC": "Primary Health Centre",
              "SHC": "Sub Health Centre", "SDH": "Sub District Hospital"}
BLOCK_NAMES = {"abhanpur": "Abhanpur", "dharseeva": "Dharsiwa", "dharsiwa": "Dharsiwa"}
TABLES = {"village": "villages", "facility": "health_facilities"}
CSV_FIELDS = ["kind", "id", "name", "block_or_type", "query", "lat", "lng", "source", "matched"]


def user_agent() -> str:
    return f"sangwari-maa-geocoder/1.0 ({os.getenv('GEOCODER_CONTACT', 'set GEOCODER_CONTACT')})"


def dsn() -> str:
    return os.environ["DATABASE_URL"].replace("+asyncpg", "")


def in_box(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def clean(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name or "")
    return re.sub(r"\s+", " ", name).strip()


def core_name(name: str) -> str:
    """'SHC Tekari N' -> 'tekari'; used to match an SHC to its same-named village."""
    s = clean(name)
    s = re.sub(r"\b(CHC|PHC|SHC|SDH|RPR|RAIPUR)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+N$", "", s.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def facility_queries(name: str, sub_district: str) -> list:
    """Most specific first; later ones are looser fallbacks."""
    s = clean(name)
    for abbr, full in TYPE_WORDS.items():
        s = re.sub(rf"\b{abbr}\b", full, s, flags=re.I)
    s = re.sub(r"\b(RPR)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+N$", "", re.sub(r"\s+", " ", s).strip())
    block = BLOCK_NAMES.get((sub_district or "").lower(), sub_district)
    return [f"{s}, {block}, Raipur, Chhattisgarh, India", f"{s}, Raipur, Chhattisgarh", f"{s}, {block}"]


def village_queries(name: str, block: str) -> list:
    n = clean(name)
    b = BLOCK_NAMES.get((block or "").lower(), (block or "").title())
    return [f"{n}, {b}, Raipur, Chhattisgarh, India", f"{n}, Raipur, Chhattisgarh", f"{n} village, {b}"]


# ── providers: each returns (lat, lng, matched_text) or None ─────────────────

def _request(req: urllib.request.Request):
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def geocode_nominatim(query: str):
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in",
        "viewbox": f"{LNG_MIN},{LAT_MAX},{LNG_MAX},{LAT_MIN}", "bounded": 1,
    })
    time.sleep(1.1)  # public Nominatim: max 1 request per second
    data = _request(urllib.request.Request(
        "https://nominatim.openstreetmap.org/search?" + params,
        headers={"User-Agent": user_agent()}))
    if not data:
        return None
    d = data[0]
    return float(d["lat"]), float(d["lon"]), d.get("display_name", "")


def geocode_google(query: str):
    body = json.dumps({
        "textQuery": query, "regionCode": "IN", "maxResultCount": 1,
        "locationRestriction": {"rectangle": {
            "low": {"latitude": LAT_MIN, "longitude": LNG_MIN},
            "high": {"latitude": LAT_MAX, "longitude": LNG_MAX}}},
    }).encode()
    data = _request(urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"],
                 "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"}))
    places = data.get("places") or []
    if not places:
        return None
    p = places[0]
    loc = p["location"]
    return (loc["latitude"], loc["longitude"],
            f'{(p.get("displayName") or {}).get("text", "")} - {p.get("formattedAddress", "")}')


GEOCODERS = {"nominatim": geocode_nominatim, "google": geocode_google}


ERRORS = 0


def try_geocode(fn, queries: list):
    """-> (result | None, query that matched or the first one tried)"""
    global ERRORS
    for q in queries:
        try:
            res = fn(q)
        except Exception as e:  # network / quota / bad key: keep going, row stays blank
            ERRORS += 1
            print(f"  ! {q!r}: {e}", file=sys.stderr)
            continue
        if res and in_box(res[0], res[1]):
            return res, q
    return None, queries[0]


# ── export ───────────────────────────────────────────────────────────────────

async def cmd_export(args):
    geocode = GEOCODERS[args.provider]
    conn = await asyncpg.connect(dsn())
    try:
        villages = await conn.fetch(
            "SELECT id, name, block, shc_id, geo_lat, geo_lng FROM villages ORDER BY block, name")
        facilities = await conn.fetch(
            """SELECT id, name, facility_type, sub_district, geo_lat, geo_lng
               FROM health_facilities
               WHERE lower(sub_district) = ANY($1::text[]) AND facility_type = ANY($2::text[])
               ORDER BY sub_district, facility_type, name""",
            PILOT_SUB_DISTRICTS, FACILITY_TYPES)
    finally:
        await conn.close()

    rows, vcoord, vname = [], {}, {}
    shc_to_villages = defaultdict(list)

    for v in villages:
        vname[v["id"]] = v["name"]
        if v["shc_id"]:
            shc_to_villages[v["shc_id"]].append(v["id"])
        if v["geo_lat"] is not None:
            vcoord[v["id"]] = (v["geo_lat"], v["geo_lng"])
            if not args.all:
                continue
        res, q = try_geocode(geocode, village_queries(v["name"], v["block"]))
        print(f"village  {'OK   ' if res else 'blank'} {q}")
        if res:
            vcoord[v["id"]] = (res[0], res[1])
        rows.append(_row("village", v["id"], v["name"], v["block"], q, res, args.provider))

    for f in facilities:
        if f["geo_lat"] is not None and not args.all:
            continue
        queries = facility_queries(f["name"], f["sub_district"])
        q = queries[0]
        res, source = None, args.provider

        if f["facility_type"] == "SHC":
            served = [vid for vid in shc_to_villages.get(f["id"], []) if vid in vcoord]
            same = [vid for vid in served if core_name(vname[vid]) == core_name(f["name"])]
            if same:
                lat, lng = vcoord[same[0]]
                res, source = (lat, lng, f"village: {vname[same[0]]}"), "village_same_name"
            elif served:
                lat = sum(vcoord[v][0] for v in served) / len(served)
                lng = sum(vcoord[v][1] for v in served) / len(served)
                res, source = (lat, lng, "mean of: " + ", ".join(vname[v] for v in served)), "village_centroid"

        if res is None:
            res, q = try_geocode(geocode, queries)
            source = args.provider
        print(f"facility {'OK   ' if res else 'blank'} {q}")
        rows.append(_row("facility", f["id"], f["name"], f["facility_type"], q, res, source))

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:  # BOM: opens cleanly in Excel
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {args.out}.")
    for kind in ("village", "facility"):
        k = [r for r in rows if r["kind"] == kind]
        print(f"  {kind}: {sum(1 for r in k if r['lat'] != '')} of {len(k)} have coordinates")
    if ERRORS:
        print(f"  {ERRORS} lookups failed with errors (see the '!' lines above) - fix those before trusting the blanks.")
    print("Review the CSV, fix anything wrong, then run the import step.")


def _row(kind, id_, name, block_or_type, query, res, source):
    return {"kind": kind, "id": str(id_), "name": name, "block_or_type": block_or_type, "query": query,
            "lat": f"{res[0]:.6f}" if res else "", "lng": f"{res[1]:.6f}" if res else "",
            "source": source if res else "", "matched": res[2] if res else ""}


# ── import ───────────────────────────────────────────────────────────────────

async def cmd_import(args):
    todo, skipped = [], 0
    with open(args.infile, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                lat, lng = float(r["lat"]), float(r["lng"])
            except (TypeError, ValueError):
                skipped += 1
                continue
            if r["kind"] not in TABLES or not in_box(lat, lng):
                print(f"  skipped (bad kind or outside Raipur box): {r['name']} {lat},{lng}", file=sys.stderr)
                skipped += 1
                continue
            todo.append((r["kind"], r["id"], lat, lng, (r.get("source") or "manual").strip() or "manual"))

    print(f"{len(todo)} rows to write, {skipped} skipped.")
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    conn = await asyncpg.connect(dsn())
    try:
        async with conn.transaction():
            for kind, id_, lat, lng, source in todo:
                await conn.execute(
                    f"UPDATE {TABLES[kind]} SET geo_lat = $1, geo_lng = $2, geo_source = $3 WHERE id = $4::uuid",
                    lat, lng, source, id_)
    finally:
        await conn.close()
    print("Done.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="geocode and write a review CSV")
    e.add_argument("--provider", choices=list(GEOCODERS), default="nominatim")
    e.add_argument("--out", default="geocode_review.csv")
    e.add_argument("--all", action="store_true", help="also redo rows that already have coordinates")
    i = sub.add_parser("import", help="load a reviewed CSV into the database")
    i.add_argument("--in", dest="infile", required=True)
    i.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(cmd_export(args) if args.cmd == "export" else cmd_import(args))


if __name__ == "__main__":
    main()