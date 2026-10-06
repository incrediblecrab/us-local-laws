"""The communities in the National Flood Insurance Program, from FEMA's Community Status Book.

FEMA says of the program that "communities agree to adopt and implement local floodplain management regulations", and its Community Status Book gives every community's standing in it. The build reads the book's national report as fema.gov publishes it, one CSV that FEMA regenerates, and takes one row per community: its number, name and county, whether it participates, the dates of its first flood maps, its current map, its entry into or sanction by the program, and its class in the Community Rating System. It then checks the report against the book as FEMA's OpenFEMA API publishes it: every community in the report must be in the API under the same name and state and with the same participation. The report changes as FEMA updates it, so a build reads it afresh, or from a snapshot harvest() took.
"""

import csv
import datetime
import hashlib
import html
import io
import json
import re
import zipfile
from collections import Counter

import pyarrow.parquet as pq

from .census import STATES, SourceChanged

CSV_URL = "https://www.fema.gov/cis/nation.csv"
PAGE = "https://www.fema.gov/flood-insurance/work-with-nfip/community-status-book"
# The whole OpenFEMA dataset in one file, as the API's dataset list gives it.
API_URL = "https://www.fema.gov/api/open/v1/NfipCommunityStatusBook.parquet"
API_PAGE = "https://www.fema.gov/openfema-data-page/nfip-community-status-book-v1"
API_NAME = "NFIP Community Status Book - v1"
API_TERMS = "https://www.fema.gov/about/openfema/terms-conditions"
REUSE = "https://www.fema.gov/about/website-information"
# What the Community Status Book's page says of the program and of the Community Rating System, and what OpenFEMA's terms say users of its API "must clearly state", quoted from each page as read on September 26, 2026.
PROGRAM_SAYS = "The National Flood Insurance Program (NFIP) enables property owners to purchase flood insurance. In return, communities agree to adopt and implement local floodplain management regulations that contribute to protecting lives and reducing the risk of new construction and substantial improvements from future flooding."
CRS_SAYS = "a voluntary incentive program that recognizes and encourages community floodplain management practices that exceed the minimum requirements of the National Flood Insurance Program (NFIP)"
REUSE_SAYS = "Most material on FEMA.gov is free of copyright and may be copied and distributed without permission."
# Where each build stores the two files it read from fema.gov, as harvest-nfip saves them, so that a run fema.gov refuses can build the table from them again; see cli.read_fema.
SNAPSHOT = "sources/nfip_snapshot.zip"
# Allow four scheduled refresh opportunities before an unreadable source fails the final freshness check.
MAX_UNVERIFIED_HOURS = 48
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
OPENFEMA_STATEMENT = "This product uses the Federal Emergency Management Agency’s OpenFEMA API, but is not endorsed by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these data after the data have been retrieved from the Agency's website(s)."
# The report's header, and the one that begins its part for communities not participating, where the eighth column gives the sanction's date instead.
COLUMNS = ["CID", "Community Name", "County", "Init FHBM Identified", "Init FIRM Identified", "Curr Eff Map Date", "Tribal", "Reg-Emer Date", "CRS Entry Date", "Curr Eff Date", "Curr Class", "% Disc", "Program", "Participating Community"]
SANCTIONED = COLUMNS[:7] + ["Sanction Date"] + COLUMNS[8:]
# A community's number is its state's FIPS code and four digits; the report prints a character after some.
TERRITORIES = {"60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI"}
CID = re.compile(r"(\d{6})([#A-Z]?)")
EXCEL = re.compile(r'="(.*)"', re.S)
DATE = re.compile(r"(?:(\d\d)/(\d\d)/(\d\d))?(?:\((.+)\))?")
# The report writes years with two digits. The program began in 1968, so 68 to 99 are read as 1968 to 1999 and the rest as 2000 to 2067; FEMA's API gives four digits for the first map dates and the Community Rating System's, which lets the build check the rule.
PIVOT = 68
# The legend's notes that may follow the date of entry or sanction, by part of the report.
STATUS_NOTES = {True: {"E"}, False: {"S", "W"}}
PROGRAMS = {"Regular", "Emergency"}
# The API's field for each of a row's, compared for every community in both.
API_FIELDS = {"community_name": "communityName", "county": "county", "state": "state", "participating": "participatingInNFIP", "tribal": "tribal"}
# Fields that must agree, else the build stops; the rest are counted.
IDENTITY = ("community_name", "state", "participating")
DATE_FIELDS = ("initial_fhbm_date", "initial_firm_date", "current_map_date", "program_entry_date", "sanction_date", "crs_entry_date", "crs_effective_date")


def cell(value):
    """A value with the ="..." the report wraps some values in, to keep a spreadsheet from reading them as numbers, removed."""
    found = EXCEL.fullmatch(value)
    return found.group(1) if found else value


def year(two):
    return 1900 + two if two >= PIVOT else 2000 + two


def dated(value, record, column):
    """(date, note) from a cell such as 09/16/11(M), (NSFHA) or blank."""
    value = value.strip()
    if not value:
        return None, None
    found = DATE.fullmatch(value)
    if not found or not (found.group(1) or found.group(4)):
        raise SourceChanged(f"record {record}: {column} is {value!r}, not a date")
    day = None
    if found.group(1):
        month, date, two = map(int, found.group(1, 2, 3))
        try:
            day = datetime.date(year(two), month, date)
        except ValueError:
            raise SourceChanged(f"record {record}: {column} is {value!r}, not a date") from None
    return day, found.group(4)


def plain(value, record, column):
    """A date that the report gives without a note."""
    day, note = dated(value, record, column)
    if note is not None:
        raise SourceChanged(f"record {record}: {column} is {value!r}, with a note this column does not take")
    return day


def number(value, pattern, record, column):
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(pattern, value):
        raise SourceChanged(f"record {record}: {column} is {value!r}")
    return int(value.rstrip("%"))


def rows(data):
    """One row per community in the report, in its order, each with the notes the report prints under it. Stops on a header, value or line the build does not know how to read."""
    try:
        records = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise SourceChanged(f"the report cannot be read as CSV: {error}") from None
    if not records or [cell(value) for value in records[0]] != COLUMNS:
        raise SourceChanged(f"the report's header is {records[0] if records else None}, not {COLUMNS}")
    out, seen, participating = [], set(), True
    for record, values in enumerate(records[1:], start=2):
        values = [cell(value) for value in values]
        if len(values) != len(COLUMNS):
            raise SourceChanged(f"record {record} has {len(values)} values, not {len(COLUMNS)}")
        if not any(value.strip() for value in values):
            continue
        if values == COLUMNS:
            if not participating:
                raise SourceChanged(f"record {record}: the participating communities' header again, after the part for those not participating")
            continue
        if values == SANCTIONED:
            if not participating:
                raise SourceChanged(f"record {record}: a second header for communities not participating")
            participating = False
            continue
        if not values[0]:
            if not out:
                raise SourceChanged(f"record {record}: a line without a community number before any community")
            name, county, others = values[1].strip(), values[2], [value for value in values[3:6] + values[7:13] if value.strip()]
            if others or bool(name) == bool(county.strip()):
                raise SourceChanged(f"record {record}: a line without a community number that is neither a note nor the rest of a county list: {values}")
            if name:
                out[-1]["notes"].append(" ".join(html.unescape(name).split()))
            else:
                # The report carries a long list of counties on to a line of its own.
                out[-1]["county"] += county
            continue
        found = CID.fullmatch(values[0])
        if not found:
            raise SourceChanged(f"record {record}: CID {values[0]!r} is not six digits and a letter or #")
        cid, suffix = found.groups()
        state = STATES.get(cid[:2]) or TERRITORIES.get(cid[:2])
        if state is None:
            raise SourceChanged(f"record {record}: CID {cid} begins with {cid[:2]}, no state's or territory's FIPS code")
        if cid in seen:
            raise SourceChanged(f"record {record}: CID {cid} is listed twice")
        seen.add(cid)
        if values[13] != ("YES" if participating else "NO"):
            raise SourceChanged(f"record {record}: Participating Community is {values[13]!r} in the part for communities {'participating' if participating else 'not participating'}")
        tribal = {"Yes": True, "No": False}.get(values[6])
        if tribal is None:
            raise SourceChanged(f"record {record}: Tribal is {values[6]!r}")
        if values[12] and values[12] not in PROGRAMS:
            raise SourceChanged(f"record {record}: Program is {values[12]!r}")
        current, map_note = dated(values[5], record, COLUMNS[5])
        status, status_note = dated(values[7], record, SANCTIONED[7] if not participating else COLUMNS[7])
        if status_note is not None and status_note not in STATUS_NOTES[participating]:
            raise SourceChanged(f"record {record}: note ({status_note}) on the date of {'entry' if participating else 'sanction'}")
        out.append({
            "report_row": record,
            "cid": cid,
            "cid_suffix": suffix or None,
            "state": state,
            "community_name": values[1].strip(),
            "county": values[2],
            "participating": participating,
            "program": values[12] or None,
            "tribal": tribal,
            "initial_fhbm_date": plain(values[3], record, COLUMNS[3]),
            "initial_firm_date": plain(values[4], record, COLUMNS[4]),
            "current_map_date": current,
            "current_map_note": map_note,
            "program_entry_date": status if participating else None,
            "sanction_date": None if participating else status,
            "status_note": status_note,
            "crs_entry_date": plain(values[8], record, COLUMNS[8]),
            "crs_effective_date": plain(values[9], record, COLUMNS[9]),
            "crs_class": number(values[10], r"\d{1,2}", record, COLUMNS[10]),
            "crs_discount": number(values[11], r"\d{2}%", record, COLUMNS[11]),
            "notes": [],
        })
    if participating:
        raise SourceChanged("the report has no part for communities not participating")
    for row in out:
        row["county"] = row["county"].strip() or None
    return out


def api_rows(data):
    try:
        return pq.read_table(io.BytesIO(data)).to_pylist()
    except Exception as error:  # pyarrow raises several types for a file that is not parquet
        raise SourceChanged(f"the OpenFEMA file cannot be read as parquet: {type(error).__name__}: {error}") from None


def api_date(value):
    return value.date() if isinstance(value, datetime.datetime) else value


def plain_value(value):
    return value.isoformat() if isinstance(value, datetime.date) else value


def api_values(item, participating):
    """The API's values for a community, in the table's terms: its dates from the report's two-digit text parsed as the build parses the report, the rest as the API gives them."""
    current, map_note = dated(item["currentlyEffectiveMapDate"] or "", item["communityIdNumber"], "currentlyEffectiveMapDate")
    status, status_note = dated((item["regularEmergencyProgramDate"] if participating else item["sanctionDate"]) or "", item["communityIdNumber"], "status date")
    discount = (item["sfhaDiscount"] or "").strip()
    return {field: item[key] for field, key in API_FIELDS.items()} | {
        # The API, like the report, ends some names with a space, which the table drops.
        "community_name": (item["communityName"] or "").strip(),
        "initial_fhbm_date": api_date(item["initialFloodHazardBoundaryMap"]),
        "initial_firm_date": api_date(item["initialFloodInsuranceRateMap"]),
        "current_map_date": current,
        "current_map_note": map_note,
        "program_entry_date": status if participating else None,
        "sanction_date": None if participating else status,
        "status_note": status_note,
        "crs_entry_date": api_date(item["originalEntryDate"]),
        "crs_effective_date": api_date(item["classRatingEffectiveDate"]),
        "crs_class": int(item["classRating"]) if (item["classRating"] or "").strip() else None,
        "crs_discount": int(discount.rstrip("%")) if discount else None,
    }


def reconcile(table, data):
    """The report's rows against the OpenFEMA file: each community must be in the API once, under the same name and state and with the same participation. Returns the counts, with the communities whose other values differ by field and the API's communities that the report does not list."""
    items = api_rows(data)
    by_id = {}
    for item in items:
        by_id.setdefault(item["communityIdNumber"], []).append(item)
    doubled = sorted(cid for cid, found in by_id.items() if len(found) > 1)
    if doubled:
        raise SourceChanged(f"the OpenFEMA file lists {len(doubled)} communities more than once: {doubled[:5]}")
    missing = [row["cid"] for row in table if row["cid"] not in by_id]
    if missing:
        raise SourceChanged(f"{len(missing):,} communities in the report are not in the OpenFEMA file: {missing[:5]}")
    differ = {}
    for row in table:
        expected = api_values(by_id[row["cid"]][0], row["participating"])
        for field, value in expected.items():
            if row[field] != value:
                differ.setdefault(field, []).append([row["cid"], plain_value(row[field]), plain_value(value)])
    identity = {field: differ[field][:5] for field in IDENTITY if field in differ}
    if identity:
        raise SourceChanged(f"the report and the OpenFEMA file disagree on {identity}")
    listed = {row["cid"] for row in table}
    only = Counter(item["participatingInNFIP"] for item in items if item["communityIdNumber"] not in listed)
    return {"rows": len(items), "in_report": len(table), "only_in_api": {"participating": only[True], "not_participating": only[False]}, "differ": dict(sorted(differ.items()))}


def now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def freshness(source, head, checked_at=None):
    checked_at = checked_at or now()
    checked = datetime.datetime.fromisoformat(checked_at)
    readings = {"retrieved_at": source.get("retrieved_at"), "api_retrieved_at": (source.get("api") or {}).get("retrieved_at")}
    ages, problems = [], []
    for name, value in readings.items():
        try:
            read = datetime.datetime.fromisoformat(value)
            if read.tzinfo is None or read > checked:
                raise ValueError("reading must have a timezone and must not be in the future")
        except (TypeError, ValueError):
            problems.append(f"FEMA {name} is missing or invalid: {value!r}")
        else:
            ages.append((checked - read).total_seconds() / 3600)
    age = max(ages) if len(ages) == len(readings) else None
    if problems:
        status = "unknown"
    elif head is not None:
        changed = source.get("sha256") != head["sha256"] or (source.get("api") or {}).get("sha256") != head["api_sha256"]
        status = "changed" if changed else "current"
        if changed:
            problems.append("Published FEMA files differ from the live source; a refresh is still needed.")
    elif age >= MAX_UNVERIFIED_HOURS:
        status = "stale"
        problems.append(f"FEMA freshness is unverified and the oldest published source reading is {age:.1f} hours old (limit {MAX_UNVERIFIED_HOURS} hours). New York updates are independent; restore supported FEMA access to refresh this source.")
    else:
        status = "unverified"
    return {"status": status, "checked_at": checked_at, **readings, "age_hours": age, "max_unverified_hours": MAX_UNVERIFIED_HOURS, "problems": problems}


def harvest(fetcher):
    """The report and the OpenFEMA file as fema.gov serves them now, with when each was read."""
    report, read_at = fetcher.get(CSV_URL), now()
    api, api_read_at = fetcher.get(API_URL), now()
    return {"csv": report, "retrieved_at": read_at, "api": api, "api_retrieved_at": api_read_at}


def snapshot_bytes(snapshot):
    """The snapshot zipped so that the same reading gives the same bytes on any machine: each member stored, not deflated, since zlib builds can deflate differently, dated ZIP_TIME and marked as made on Unix, which zipfile otherwise takes from the platform. A build commits these bytes only when they change."""
    buffer = io.BytesIO()
    read = json.dumps({"csv_url": CSV_URL, "retrieved_at": snapshot["retrieved_at"], "api_url": API_URL, "api_retrieved_at": snapshot["api_retrieved_at"]}, indent=1) + "\n"
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (("nation.csv", snapshot["csv"]), ("NfipCommunityStatusBook.parquet", snapshot["api"]), ("read.json", read.encode())):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def save(snapshot, path):
    with open(path, "wb") as handle:
        handle.write(snapshot_bytes(snapshot))


def load(source):
    """source is a path to a saved snapshot, or its bytes."""
    with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as archive:
        read = json.loads(archive.read("read.json"))
        return {"csv": archive.read("nation.csv"), "retrieved_at": read["retrieved_at"], "api": archive.read("NfipCommunityStatusBook.parquet"), "api_retrieved_at": read["api_retrieved_at"]}


def differs(snapshot, source):
    """Which of the snapshot's files and reading times are not the ones source, a manifest's nfip_communities record, gives; [] when it is that reading."""
    api = source.get("api") or {}
    read = {"nation.csv": hashlib.sha256(snapshot["csv"]).hexdigest(), "retrieved_at": snapshot["retrieved_at"], "NfipCommunityStatusBook.parquet": hashlib.sha256(snapshot["api"]).hexdigest(), "api_retrieved_at": snapshot["api_retrieved_at"]}
    recorded = {"nation.csv": source.get("sha256"), "retrieved_at": source.get("retrieved_at"), "NfipCommunityStatusBook.parquet": api.get("sha256"), "api_retrieved_at": api.get("retrieved_at")}
    return sorted(key for key in read if read[key] != recorded[key])


def after(table, day):
    """The dates later than day, the day the report was read: [cid, community_name, column, date, note] for each."""
    out = []
    for row in table:
        for column in DATE_FIELDS:
            if row[column] is not None and row[column] > day:
                note = row["current_map_note"] if column == "current_map_date" else None
                out.append([row["cid"], row["community_name"], column, row[column].isoformat(), note])
    return out


def source(snapshot, table, reconciled):
    """The manifest's record of the report as read and of its check against the OpenFEMA file."""
    read_day = datetime.date.fromisoformat(snapshot["retrieved_at"][:10])
    return {
        "url": CSV_URL,
        "page": PAGE,
        "sha256": hashlib.sha256(snapshot["csv"]).hexdigest(),
        "bytes": len(snapshot["csv"]),
        "retrieved_at": snapshot["retrieved_at"],
        "rows": len(table),
        "participating": sum(1 for row in table if row["participating"]),
        "with_notes": sum(1 for row in table if row["notes"]),
        "after_retrieval": after(table, read_day),
        "api": {"url": API_URL, "page": API_PAGE, "name": API_NAME, "sha256": hashlib.sha256(snapshot["api"]).hexdigest(), "retrieved_at": snapshot["api_retrieved_at"]} | reconciled,
    }


def stats(table):
    """The counts the card shows for the table; verify recomputes them from the published rows."""
    joined = [row for row in table if row["participating"]]
    return {
        "rows": len(table),
        "participating": len(joined),
        "not_participating": len(table) - len(joined),
        "programs": {program: sum(1 for row in joined if row["program"] == program) for program in sorted(PROGRAMS, reverse=True)} | {"none": sum(1 for row in joined if row["program"] is None)},
        "status_notes": dict(sorted(Counter(row["status_note"] for row in table if row["status_note"]).items())),
        "map_notes": dict(sorted(Counter(row["current_map_note"] for row in table if row["current_map_note"]).items())),
        "tribal": sum(1 for row in table if row["tribal"]),
        "tribal_participating": sum(1 for row in joined if row["tribal"]),
        "crs": sum(1 for row in table if row["crs_class"] is not None),
        "crs_classes": {str(n): {"communities": count, "discounts": sorted({row["crs_discount"] for row in table if row["crs_class"] == n} - {None})}
                        for n, count in sorted(Counter(row["crs_class"] for row in table if row["crs_class"] is not None).items())},
        "with_notes": sum(1 for row in table if row["notes"]),
    }
