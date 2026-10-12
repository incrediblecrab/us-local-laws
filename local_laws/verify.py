"""Checks a published build: its files against manifest.json, its tables against their schemas and each other, its counts against the Census's own table, LOCUS's card and the New York API's counts at the harvest, New York's index against its release, the Tribes against the Bureau of Indian Affairs' notices, the flood insurance communities against FEMA's report, and its card against a fresh render."""

import datetime
import hashlib
import json
import re
import zipfile
from collections import Counter

import httpx
import pyarrow as pa

from . import census, locus, nfip, nyindex, nylaws, tribes
from .build import summarize
from .card import render
from .http import Blocked, Unavailable
from .schema import TABLES
from .store import CARD, HUB_FILES, MANIFEST

SAMPLE = 10
CENSUS_ID = re.compile(r"\d{6}")


def sample(values):
    values = sorted(values, key=lambda value: (value is None, value if value is not None else 0))
    return f"{len(values):,} ({', '.join(map(str, values[:SAMPLE]))}{', ...' if len(values) > SAMPLE else ''})"


def check_governments(rows, problems):
    ids = [row["census_id"] for row in rows]
    malformed = [uid for uid in ids if uid is None or not CENSUS_ID.fullmatch(uid)]
    if malformed:
        problems.append(f"governments: census_id null or not 6 digits: {sample(map(repr, malformed))}")
    duplicates = [uid for uid, n in Counter(ids).items() if uid is not None and n > 1]
    if duplicates:
        problems.append(f"governments: duplicate census_id: {sample(duplicates)}")
    kinds = Counter(row["government_type"] for row in rows)
    if set(kinds) - set(census.TYPES):
        problems.append(f"governments: government_type values {sorted(set(kinds) - set(census.TYPES))} are not the Census's five types")
    misplaced = [row["census_id"] for row in rows if census.STATES.get(row["state_fips"]) != row["state"]]
    if misplaced:
        problems.append(f"governments: state does not match state_fips: {sample(misplaced)}")
    general = set(locus.GENERAL_PURPOSE)
    stray = [row["census_id"] for row in rows if (row["government_type"] in general) != (row["fips_place"] is not None)]
    if stray:
        problems.append(f"governments: fips_place set for a district or missing for a general-purpose government: {sample(stray)}")


def check_crosswalk(rows, government_ids, problems):
    """government_ids is None when the governments table was not read, so no census_id is judged against it."""
    keys = Counter((row["locus_state"], row["locus_jurisdiction_type"], row["locus_name"]) for row in rows)
    repeated = [f"{state}/{kind}/{name}" for (state, kind, name), n in keys.items() if n > 1]
    if repeated:
        problems.append(f"locus_crosswalk: jurisdictions listed more than once: {sample(repeated)}")
    unknown = sorted({row["match"] for row in rows} - set(locus.MATCHES))
    if unknown:
        problems.append(f"locus_crosswalk: match values {unknown} are not among {list(locus.MATCHES)}")
    unmatched = set(locus.MATCHES) - set(locus.MATCHED)
    for row in rows:
        name = f"{row['locus_state']}/{row['locus_name']}"
        if (row["census_id"] is None) != (row["match"] in unmatched):
            problems.append(f"locus_crosswalk: {name} is {row['match']} with census_id {row['census_id']!r}")
        if government_ids is not None and row["census_id"] is not None and row["census_id"] not in government_ids:
            problems.append(f"locus_crosswalk: {name} names census_id {row['census_id']}, which is not in governments")
        if bool(row["candidates"]) != (row["match"] in locus.POOLED):
            problems.append(f"locus_crosswalk: {name} is {row['match']} with candidates {row['candidates']}")
        if row["census_id"] is not None and row["candidates"] and row["census_id"] not in row["candidates"]:
            problems.append(f"locus_crosswalk: {name}'s census_id {row['census_id']} is not among its candidates")
        if row["locus_rows"] is None or row["locus_rows"] < 1:
            problems.append(f"locus_crosswalk: {name} has locus_rows {row['locus_rows']!r}")
    duplicates = [uid for uid, n in Counter(row["census_id"] for row in rows if row["census_id"]).items() if n > 1]
    if duplicates:
        problems.append(f"locus_crosswalk: census_id matched by more than one jurisdiction: {sample(duplicates)}")


def check_ny(rows, governments, source, problems):
    """governments is None when that table was not read, so no census_id is judged against it. source is the manifest's record of the harvest."""
    ids = Counter(row["asset_id"] for row in rows)
    if None in ids or "" in ids:
        problems.append(f"ny_local_laws: {ids[None] + ids['']:,} rows without asset_id")
    duplicates = [uid for uid, n in ids.items() if uid and n > 1]
    if duplicates:
        problems.append(f"ny_local_laws: duplicate asset_id: {sample(duplicates)}")
    counted = source.get("years") or {}
    if sum(counted.values()) != source.get("total"):
        problems.append(f"ny_local_laws: the manifest's API counts by year add to {sum(counted.values()):,}, not its total {source.get('total')}")
    if len(rows) != source.get("total"):
        problems.append(f"ny_local_laws: {len(rows):,} rows; the API counted {source.get('total')} filings at the harvest")
    years = {str(year): n for year, n in Counter(row["date_filed"].year for row in rows if row["date_filed"]).items()}
    differing = sorted(year for year in set(years) | set(counted) if years.get(year, 0) != counted.get(year, 0))
    if differing:
        problems.append(f"ny_local_laws: filings by year differ from the API's counts at the harvest in {sample(differing)}")
    links = [row["asset_id"] for row in rows if not row["share_url"] or not nylaws.SHARE.fullmatch(row["share_url"])]
    if links:
        problems.append(f"ny_local_laws: share_url is not the Department's public link: {sample(links)}")
    check_matches("ny_local_laws", rows, "asset_id", governments, problems)


def check_matches(name, rows, key, governments, problems, read_type=None):
    """The match of a New York table's rows, each named by its key column: census_id, match and candidates agree, a census_id is a New York government with the Census title the row's type gives, read by read_type if given, and all three are what the matching rules give the row's type and name.
    governments is None when that table was not read, so no census_id is judged against it."""
    unknown = sorted({row["match"] for row in rows} - set(nylaws.MATCHES))
    if unknown:
        problems.append(f"{name}: match values {unknown} are not among {list(nylaws.MATCHES)}")
    titles = None if governments is None else {row["census_id"]: nylaws.split_title(row["name"])[0] for row in governments if row["state"] == "NY"}
    inconsistent, stray, mistitled = [], [], []
    for row in rows:
        unmatched = row["match"] not in nylaws.MATCHED
        if (row["census_id"] is None) != unmatched or bool(row["candidates"]) != (row["match"] == "ambiguous"):
            inconsistent.append(row[key])
        elif titles is not None and row["census_id"] is not None:
            kind = row["municipality_type"] if read_type is None else read_type(row["municipality_type"])
            if row["census_id"] not in titles:
                stray.append(row[key])
            elif titles[row["census_id"]] != nylaws.TITLES.get(kind):
                mistitled.append(row[key])
    if inconsistent:
        problems.append(f"{name}: census_id, match and candidates disagree: {sample(inconsistent)}")
    if stray:
        problems.append(f"{name}: census_id is not a New York government in governments: {sample(stray)}")
    if mistitled:
        problems.append(f"{name}: census_id's Census title is not the one municipality_type gives: {sample(mistitled)}")
    if titles is None:
        return
    try:
        again = nylaws.match([{"municipality_type": row["municipality_type"], "municipality_name": row["municipality_name"]} for row in rows], governments, read_type)
    except census.SourceChanged as error:
        problems.append(f"{name}: the match cannot be recomputed from governments: {error}")
        return
    rematched = [row[key] for row, fresh in zip(rows, again) if (row["census_id"], row["match"], row["candidates"]) != (fresh["census_id"], fresh["match"], fresh["candidates"])]
    if rematched:
        problems.append(f"{name}: census_id, match and candidates are not what the matching rules give the row's type and name: {sample(rematched)}")


def check_ny_index(rows, governments, source, problems):
    """governments is None when that table was not read, so no census_id is judged against it. source is the manifest's record of the release."""
    positions = Counter(row["index_row"] for row in rows)
    if sorted(positions) != list(range(1, len(rows) + 1)):
        repeated = [n for n, count in positions.items() if count > 1]
        problems.append(f"ny_local_law_index: index_row is not 1 to {len(rows):,}, once each" + (f"; repeated: {sample(repeated)}" if repeated else ""))
    for field in ("rows", "state_records"):
        if len(rows) != source.get(field):
            problems.append(f"ny_local_law_index: {len(rows):,} rows; the manifest's source gives {field} {source.get(field)}")
    check_matches("ny_local_law_index", rows, "index_row", governments, problems, nyindex.read_type)


def compare_ny_index(rows, data, problems):
    """The published rows against the pinned release: each row equal to the export's line at its index_row, and all of them, field for field, the State's records."""
    export = {row["index_row"]: row for row in nyindex.rows(census.zip_member(data, nyindex.EXPORT_MEMBER))}
    state = nyindex.state_records(census.zip_member(data, nyindex.STATE_MEMBER))
    changed = [row["index_row"] for row in rows if {field: row[field] for field in nyindex.FIELDS} != export.get(row["index_row"])]
    if changed:
        problems.append(f"ny_local_law_index: rows that are not the release's export line at their index_row: {sample(changed)}")
    missing = sorted(set(export) - {row["index_row"] for row in rows})
    if missing:
        problems.append(f"ny_local_law_index: export lines not in the table: {sample(missing)}")
    published, held = Counter(map(nyindex.record, rows)), Counter(state)
    if published != held:
        problems.append(f"ny_local_law_index: {sum((published - held).values()):,} rows are not records in the State's {nyindex.STATE_MEMBER}, and {sum((held - published).values()):,} records are not rows")
    return len(state)


def check_tribes(rows, source, problems):
    """source is the manifest's record of the two notices."""
    name = "federally_recognized_tribes"
    positions = Counter(row["list_row"] for row in rows)
    if set(positions) != set(range(1, len(rows) + 1)):
        repeated = [n for n, count in positions.items() if count > 1]
        problems.append(f"{name}: list_row is not 1 to {len(rows):,}, once each" + (f"; repeated: {sample(repeated)}" if repeated else ""))
    order = list(tribes.LISTS)
    unknown = sorted({row["list"] for row in rows} - set(order), key=str)
    if unknown:
        problems.append(f"{name}: list values {unknown} are not among {order}")
    else:
        places = [order.index(row["list"]) for row in sorted(rows, key=lambda row: (row["list_row"] is None, row["list_row"] or 0))]
        if places != sorted(places):
            problems.append(f"{name}: the lists are not in the notice's order, {' then '.join(order)}")
    misnamed = [row["list_row"] for row in rows if row["name"] != tribes.name(row["entry"] or "")]
    if misnamed:
        problems.append(f"{name}: name is not the entry's text before its first parenthesis: {sample(misnamed)}")
    continued = Counter((row["list"], row["previous_entry"]) for row in rows if row["previous_entry"] is not None)
    doubled = [f"{kind}/{entry}" for (kind, entry), n in continued.items() if n > 1]
    if doubled:
        problems.append(f"{name}: earlier entries continued by more than one row: {sample(doubled)}")
    for field in ("rows", "entries"):
        if len(rows) != source.get(field):
            problems.append(f"{name}: {len(rows):,} rows; the manifest's source gives {field} {source.get(field)}")
    counts = {field: source.get(field) for field in ("stated", "entries", "previous_stated", "previous_entries")}
    if not all(isinstance(value, int) for value in counts.values()):
        problems.append(f"{name}: the manifest's source gives counts {counts}")
        return
    added = sum(1 for row in rows if row["previous_entry"] is None)
    if added != counts["stated"] - counts["previous_stated"]:
        problems.append(f"{name}: {added:,} rows continue no earlier entry; the stated count went from {counts['previous_stated']:,} to {counts['stated']:,}")
    if len(rows) - added != counts["previous_entries"]:
        problems.append(f"{name}: {len(rows) - added:,} rows continue an earlier entry; the manifest's source gives the earlier notice {counts['previous_entries']:,} entries")


def compare_tribes(rows, fetcher, source, problems):
    """The published rows against the two notices, downloaded again at their pinned SHA-256: every row, field for field, what the build takes from them, and the manifest's counts what they hold."""
    notice, previous, fresh = tribes.load(fetcher)
    expected = {row["list_row"]: row for row in fresh}
    changed = [row["list_row"] for row in rows if {field: row[field] for field in tribes.FIELDS} != expected.get(row["list_row"])]
    if changed:
        problems.append(f"federally_recognized_tribes: rows that are not the notice's entry at their list_row: {sample(changed)}")
    missing = sorted(set(expected) - {row["list_row"] for row in rows})
    if missing:
        problems.append(f"federally_recognized_tribes: entries of the notice not in the table: {sample(missing)}")
    counts = {"stated": notice["stated"], "entries": len(notice["entries"]), "previous_stated": previous["stated"], "previous_entries": len(previous["entries"])}
    differing = sorted(field for field, value in counts.items() if source.get(field) != value)
    if differing:
        problems.append(f"federally_recognized_tribes: the manifest's source differs from the notices in {differing}")
    return len(fresh)


def check_nfip(rows, source, problems):
    """source is the manifest's record of FEMA's report and of its check against the OpenFEMA file."""
    name = "nfip_communities"
    cids = [row["cid"] for row in rows]
    malformed = [cid for cid in cids if cid is None or not CENSUS_ID.fullmatch(cid)]
    if malformed:
        problems.append(f"{name}: cid null or not 6 digits: {sample(map(repr, malformed))}")
    for field, values in (("cid", cids), ("report_row", [row["report_row"] for row in rows])):
        repeated = [value for value, n in Counter(values).items() if value is not None and n > 1]
        if repeated:
            problems.append(f"{name}: duplicate {field}: {sample(repeated)}")
    misplaced = [row["cid"] for row in rows if row["cid"] and (census.STATES.get(row["cid"][:2]) or nfip.TERRITORIES.get(row["cid"][:2])) != row["state"]]
    if misplaced:
        problems.append(f"{name}: state is not the one whose FIPS code begins cid: {sample(misplaced)}")
    inconsistent = [row["cid"] for row in rows if (row["sanction_date"] if row["participating"] else row["program_entry_date"]) is not None
                    or (row["status_note"] is not None and row["status_note"] not in nfip.STATUS_NOTES.get(row["participating"], ()))]
    if inconsistent:
        problems.append(f"{name}: a date or note of entry for a community not participating, or of sanction for one participating: {sample(inconsistent)}")
    ordered = [row["participating"] for row in sorted(rows, key=lambda row: (row["report_row"] is None, row["report_row"] or 0))]
    if ordered != sorted(ordered, reverse=True):
        problems.append(f"{name}: communities not participating come before participating ones in report_row order, unlike the report's two parts")
    counts = {"rows": len(rows), "participating": sum(1 for row in rows if row["participating"]), "with_notes": sum(1 for row in rows if row["notes"])}
    differing = {field: (value, source.get(field)) for field, value in counts.items() if source.get(field) != value}
    api = source.get("api") or {}
    if api.get("in_report") != len(rows):
        differing["api in_report"] = (len(rows), api.get("in_report"))
    try:
        after = nfip.after(rows, datetime.date.fromisoformat(str(source.get("retrieved_at"))[:10]))
    except (TypeError, ValueError):
        after = None
    if after != source.get("after_retrieval"):
        differing["after_retrieval"] = (None if after is None else len(after), len(source.get("after_retrieval") or []))
    if differing:
        problems.append(f"{name}: the table and the manifest's source disagree, (table, manifest): {differing}")


def compare_nfip(rows, fetcher, source, problems):
    """The published rows against FEMA's report read again: when it is still the file the build read, every row field for field. FEMA regenerates the report, so a different file is reported, not a problem; so is a refusal, since fema.gov has refused GitHub-hosted runners, and check_nfip_snapshot compares the rows without it."""
    try:
        data = fetcher.get(nfip.CSV_URL)
    except (Blocked, Unavailable, httpx.HTTPStatusError) as error:
        return {"same_file": None, "unreadable": f"{type(error).__name__}: {error}"}
    if hashlib.sha256(data).hexdigest() != source.get("sha256"):
        return {"same_file": False}
    return {"same_file": True, "rows": same_rows(rows, data, "the report", problems)}


def same_rows(rows, data, report, problems):
    """Names each published row that is not the one the report data gives at its cid, and each community the report has that the table lacks; returns the report's row count."""
    expected = {row["cid"]: row for row in nfip.rows(data)}
    changed = [row["cid"] for row in rows if row != expected.get(row["cid"])]
    if changed:
        problems.append(f"nfip_communities: rows that are not {report}'s community at their cid: {sample(changed)}")
    missing = sorted(set(expected) - {row["cid"] for row in rows})
    if missing:
        problems.append(f"nfip_communities: communities in {report} not in the table: {sample(missing)}")
    return len(expected)


def check_nfip_snapshot(rows, store, source, problems):
    """The FEMA files the build stored against the manifest's record of what it read, then the published rows against the rows the stored report gives. It needs no network, so it runs where fema.gov refuses requests, and it passes only when a run that fema.gov refuses can build from the stored files."""
    try:
        snapshot = nfip.load(store.read_bytes(nfip.SNAPSHOT))
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError) as error:
        problems.append(f"{nfip.SNAPSHOT} cannot be read as a snapshot: {type(error).__name__}: {error}")
        return None
    differ = nfip.differs(snapshot, source)
    if differ:
        problems.append(f"{nfip.SNAPSHOT} is not the reading the manifest's nfip_communities source records: its {differ} differ")
        return {"same_file": False}
    return {"same_file": True, "rows": same_rows(rows, snapshot["csv"], "the stored report", problems)}


def verify(store, fetcher=None, stated_rows=None):
    """A report whose "problems" is empty when every check passed. With a fetcher, CG2200ORG02, New York's index, the Bureau of Indian Affairs' two notices and FEMA's report are downloaded again and compared; with stated_rows (a callable), LOCUS's card is read again."""
    text = store.read_text(MANIFEST)
    if text is None:
        return {"problems": [f"no {MANIFEST}"]}
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        return {"problems": [f"{MANIFEST} is not JSON: {error}"]}
    problems = []
    entries = manifest.get("files") or {}
    expected = set(entries) | {MANIFEST, CARD}
    listed = set(store.list_files())
    for path in sorted(expected - listed):
        problems.append(f"{path} is in the manifest but not in the repo")
    for path in sorted(listed - expected - set(HUB_FILES) - {nfip.HARVEST}):
        problems.append(f"{path} is in the repo but not in the manifest")
    sha256s = store.file_sha256s(sorted(set(entries) & listed))
    for path, entry in sorted(entries.items()):
        if path in listed and sha256s.get(path) != entry.get("sha256"):
            problems.append(f"{path} has SHA-256 {str(sha256s.get(path))[:12]}, the manifest says {str(entry.get('sha256'))[:12]}")
    tables = {}
    for name, spec in TABLES.items():
        if set(spec["docs"]) != set(spec["schema"].names):
            problems.append(f"{name}: documented columns differ from the schema's: {sorted(set(spec['docs']) ^ set(spec['schema'].names))}")
        if spec["file"] not in entries:
            problems.append(f"{name}: {spec['file']} is not in the manifest")
            continue
        try:
            table = store.read_table(spec["file"]) if spec["file"] in listed else None
        except (pa.ArrowException, OSError) as error:
            problems.append(f"{name}: {spec['file']} cannot be read as parquet: {type(error).__name__}: {error}")
            continue
        if table is None:
            continue
        if not table.schema.equals(spec["schema"], check_metadata=False):
            problems.append(f"{name}: schema {table.schema.to_string(show_schema_metadata=False)!r} is not the documented one")
            continue
        if table.num_rows != entries[spec["file"]].get("rows"):
            problems.append(f"{name}: {table.num_rows:,} rows, the manifest says {entries[spec['file']].get('rows')}")
        tables[name] = table.to_pylist()
    report = {"rows": {name: len(rows) for name, rows in tables.items()}}
    sources = manifest.get("sources") or {}
    pins = {("census_governments", "sha256"): census.GOVT_UNITS_SHA256, ("census_org02", "sha256"): census.ORG02_SHA256, ("locus", "revision"): locus.REVISION, ("ny_local_law_index", "sha256"): nyindex.SHA256,
            ("federally_recognized_tribes", "sha256"): tribes.NOTICE["sha256"], ("federally_recognized_tribes", "previous_sha256"): tribes.PREVIOUS["sha256"]}
    for (source, field), pin in pins.items():
        if (sources.get(source) or {}).get(field) != pin:
            problems.append(f"the manifest's {source} {field} is {(sources.get(source) or {}).get(field)!r}; this code pins {pin}")
    governments, crosswalk = tables.get("governments"), tables.get("locus_crosswalk")
    if governments is not None:
        check_governments(governments, problems)
        if len(governments) != (sources.get("census_governments") or {}).get("rows"):
            problems.append(f"governments: {len(governments):,} rows, the manifest's source says {(sources.get('census_governments') or {}).get('rows')}")
        if fetcher is not None:
            data = fetcher.get(census.ORG02_URL)
            census.check_sha256(data, census.ORG02_SHA256, census.ORG02_URL)
            org02 = census.parse_org02(census.zip_member(data, census.ORG02_MEMBER))
            mismatches = census.compare(governments, org02)
            problems += [f"CG2200ORG02: {mismatch}" for mismatch in mismatches]
            report["org02_counts_compared"] = len(org02)
    if crosswalk is not None:
        check_crosswalk(crosswalk, None if governments is None else {row["census_id"] for row in governments}, problems)
        read = sum(row["locus_rows"] or 0 for row in crosswalk)
        if read != (sources.get("locus") or {}).get("rows"):
            problems.append(f"locus_crosswalk: {read:,} LOCUS rows, the manifest says {(sources.get('locus') or {}).get('rows')}")
        if stated_rows is not None:
            stated = stated_rows()
            report["locus_stated_rows"] = stated
            if read != stated:
                problems.append(f"locus_crosswalk: {read:,} LOCUS rows, LOCUS's card states {stated:,}")
    ny = tables.get("ny_local_laws")
    if ny is not None:
        check_ny(ny, governments, sources.get("ny_local_laws") or {}, problems)
    index = tables.get("ny_local_law_index")
    if index is not None:
        check_ny_index(index, governments, sources.get("ny_local_law_index") or {}, problems)
        if fetcher is not None:
            report["ny_index_state_records"] = compare_ny_index(index, nyindex.download(fetcher), problems)
    recognized = tables.get("federally_recognized_tribes")
    if recognized is not None:
        check_tribes(recognized, sources.get("federally_recognized_tribes") or {}, problems)
        if fetcher is not None:
            report["tribes_notice_entries"] = compare_tribes(recognized, fetcher, sources.get("federally_recognized_tribes") or {}, problems)
    communities = tables.get("nfip_communities")
    if communities is not None:
        check_nfip(communities, sources.get("nfip_communities") or {}, problems)
        if nfip.SNAPSHOT in entries and nfip.SNAPSHOT in listed:
            report["nfip_snapshot"] = check_nfip_snapshot(communities, store, sources.get("nfip_communities") or {}, problems)
        if fetcher is not None:
            report["nfip_report"] = compare_nfip(communities, fetcher, sources.get("nfip_communities") or {}, problems)
    if governments is not None and crosswalk is not None and ny is not None and index is not None and recognized is not None and communities is not None:
        try:
            stats = json.loads(json.dumps(summarize(governments, crosswalk, ny, index, recognized, communities)))
        except (KeyError, TypeError) as error:
            problems.append(f"the manifest's stats cannot be recomputed from the tables: {type(error).__name__}: {error}")
        else:
            if stats != manifest.get("stats"):
                differing = sorted(key for key in set(stats) | set(manifest.get("stats") or {}) if stats.get(key) != (manifest.get("stats") or {}).get(key))
                problems.append(f"the manifest's stats differ from the tables' in {differing}")
    try:
        card = render(manifest)
    except (KeyError, TypeError, ValueError) as error:
        problems.append(f"the card cannot be rendered from the manifest: {type(error).__name__}: {error}")
    else:
        if store.read_text(CARD) != card:
            problems.append(f"{CARD} is not the card this code renders from the manifest; run `python -m local_laws card`")
    report["problems"] = problems
    return report
