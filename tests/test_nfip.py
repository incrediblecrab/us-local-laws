"""FEMA's Community Status Book: the report read into one row per community, checked against OpenFEMA's copy, saved and loaded as a snapshot, and the counts the card shows."""

import csv
import datetime
import io
import re
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import NFIP_API, NFIP_CSV, FakeFetcher, nfip_snapshot
from local_laws import nfip
from local_laws.census import SourceChanged

ROWS = nfip.rows(NFIP_CSV)


def community(cid, rows=ROWS):
    return next(row for row in rows if row["cid"] == cid)


def edited(old, new, data=NFIP_CSV):
    text = data.decode("utf-8")
    assert text.count(old) == 1, old
    return text.replace(old, new).encode("utf-8")


def api_edited(edit):
    items = pq.read_table(io.BytesIO(NFIP_API)).to_pylist()
    edit(items)
    out = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(items, schema=pq.read_schema(io.BytesIO(NFIP_API))), out)
    return out.getvalue()


@pytest.mark.parametrize("seconds, status", [(-1, "unverified"), (0, "stale"), (1, "stale")])
def test_freshness_alert_begins_at_the_exact_age_limit(seconds, status):
    read = datetime.datetime(2026, 10, 4, tzinfo=datetime.UTC)
    source = {"retrieved_at": read.isoformat(), "api": {"retrieved_at": read.isoformat()}}
    checked = read + datetime.timedelta(hours=nfip.MAX_UNVERIFIED_HOURS, seconds=seconds)
    report = nfip.freshness(source, None, checked.isoformat())
    assert report["status"] == status
    assert bool(report["problems"]) == (status == "stale")
    assert report["age_hours"] == nfip.MAX_UNVERIFIED_HOURS + seconds / 3600


def test_live_matching_hashes_establish_freshness_without_rewriting_reading_dates():
    source = {"retrieved_at": "2026-09-26T08:18:00Z", "sha256": "csv", "api": {"retrieved_at": "2026-09-26T08:23:00Z", "sha256": "api"}}
    head = {"sha256": "csv", "api_sha256": "api"}
    report = nfip.freshness(source, head, "2026-10-06T12:00:00Z")
    assert report["status"] == "current" and report["problems"] == []
    assert report["retrieved_at"] == source["retrieved_at"] and report["age_hours"] > nfip.MAX_UNVERIFIED_HOURS
    for key in head:
        changed = nfip.freshness(source, dict(head, **{key: "changed"}), "2026-10-06T12:00:00Z")
        assert changed["status"] == "changed" and changed["problems"]


def test_freshness_uses_the_older_of_the_two_source_readings():
    source = {"retrieved_at": "2026-10-06T08:18:00Z", "api": {"retrieved_at": "2026-09-26T08:23:00Z"}}
    assert nfip.freshness(source, None, "2026-10-06T12:00:00Z")["status"] == "stale"


@pytest.mark.parametrize("value", [None, "", "not a date", "2026-10-06T08:18:00", "2026-10-07T00:00:00Z"])
@pytest.mark.parametrize("field", ["csv", "api"])
def test_missing_invalid_naive_or_future_readings_cannot_pass_freshness(value, field):
    source = {"retrieved_at": value if field == "csv" else "2026-10-06T08:18:00Z", "api": {"retrieved_at": value if field == "api" else "2026-10-06T08:23:00Z"}}
    report = nfip.freshness(source, None, "2026-10-06T12:00:00Z")
    assert report["status"] == "unknown" and report["age_hours"] is None and report["problems"]


def line(text):
    """The sample's one line holding text."""
    found = [each for each in NFIP_CSV.decode("utf-8").splitlines() if text in each]
    assert len(found) == 1, text
    return found[0]


def test_the_sample_reads_as_one_row_per_community_in_the_reports_order():
    assert len(ROWS) == 25 and [row["participating"] for row in ROWS] == [True] * 20 + [False] * 5
    assert [row["report_row"] for row in ROWS] == sorted(row["report_row"] for row in ROWS)
    assert (ROWS[0]["cid"], ROWS[0]["report_row"]) == ("010504", 2)
    assert community("010224")["report_row"] == community("010371")["report_row"] + 2, "the header the report repeats is skipped, not read as a community"
    assert ROWS[20]["cid"] == "010095", "the second part begins after its own header"


@pytest.mark.parametrize("blank", [[""] * 14, ["", "\n", "", "", "", "", "", "", "", "", "", "", "", ""], [" \t"] * 14])
def test_whitespace_only_records_are_not_communities_or_notes(blank):
    records = list(csv.reader(io.StringIO(NFIP_CSV.decode(), newline="")))
    records.insert(2, blank)
    output = io.StringIO(newline="")
    csv.writer(output).writerows(records)
    parsed = nfip.rows(output.getvalue().encode())
    assert len(parsed) == len(ROWS)
    for actual, expected in zip(parsed, ROWS):
        assert actual == dict(expected, report_row=expected["report_row"] + (expected["report_row"] > 2))


def test_values_are_read_as_the_report_gives_them():
    birmingham = community("010116")
    assert birmingham == {
        "report_row": 3, "cid": "010116", "cid_suffix": "B", "state": "AL", "community_name": "BIRMINGHAM, CITY OF", "county": "SHELBY COUNTY/JEFFERSON COUNTY",
        "participating": True, "program": "Regular", "tribal": False, "initial_fhbm_date": datetime.date(1974, 6, 28), "initial_firm_date": datetime.date(1981, 3, 16),
        "current_map_date": datetime.date(2021, 9, 24), "current_map_note": None, "program_entry_date": datetime.date(1981, 3, 16), "sanction_date": None, "status_note": None,
        "crs_entry_date": datetime.date(1994, 10, 1), "crs_effective_date": datetime.date(2022, 10, 1), "crs_class": 5, "crs_discount": 25, "notes": ["INCLUDES THE TOWNS OF BROWNVILLE AND ROOSEVELT"],
    }
    assert community("010465")["community_name"] == "GOLDVILLE, TOWN OF" and '"GOLDVILLE, TOWN OF "' in line("GOLDVILLE"), "a trailing space is dropped"


def test_codes_beside_dates_are_split_from_them():
    assert (community("010504")["current_map_date"], community("010504")["current_map_note"]) == (datetime.date(2011, 9, 16), "M")
    assert (community("010350")["current_map_date"], community("010350")["current_map_note"]) == (datetime.date(2009, 8, 18), "L")
    assert (community("010465")["current_map_date"], community("010465")["current_map_note"]) == (None, "NSFHA")
    assert (community("080037")["current_map_date"], community("080037")["current_map_note"]) == (None, "All Zone D")
    assert community("080296")["current_map_note"] == ">" and community("080296")["current_map_date"] > datetime.date(2026, 9, 26)
    assert (community("020127")["program"], community("020127")["program_entry_date"], community("020127")["status_note"]) == ("Emergency", datetime.date(2002, 1, 15), "E")
    assert [(community(cid)["status_note"], community(cid)["program"]) for cid in ("010214", "020040", "010095")] == [("S", "Regular"), ("W", "Regular"), (None, None)]
    assert (community("010214")["sanction_date"], community("010214")["program_entry_date"]) == (datetime.date(2009, 1, 7), None)


def test_two_digit_years_are_read_as_1968_to_2067():
    assert community("380146")["current_map_date"] == datetime.date(2050, 1, 2)
    assert community("025009")["initial_firm_date"] == datetime.date(1969, 6, 25)
    assert [nfip.year(two) for two in (67, 68, 99, 0)] == [2067, 1968, 1999, 2000]


def test_the_lines_beneath_a_community_are_its_notes_or_the_rest_of_its_county():
    assert community("010153")["county"] == "MORGAN COUNTY/LIMESTONE COUNTY/MADISON COUNTY" and '"MORGAN COUNTY/LIMESTONE "' in line("HUNTSVILLE"), "the report carries the list on to a line of its own"
    assert community("120425")["notes"] == ["THE VILLAGE OF PINECREST HAS ADOPTED THE DADE COUNTY (120635) FIRM PANELS 260, 276, & 278. THE INITIAL FIRM DATE FOR THE COMMUNITY IS 10/29/1972 FOR FLOODPLAIN MANAGEMENT PURPOSES."]
    assert b"&amp; 278" in NFIP_CSV and b"FIRM PANELS 260, 276, &amp; 278. THE INITIAL FIRM DATE" not in NFIP_CSV, "the report escapes the & and breaks the note over lines"
    assert community("120665")["notes"] == ["USE POLK COUNTY FIRM (CID 120261) INDEX DATED DECEMBER 20, 2000, PANELS", "12105C0180E, 12105C0190E AND 12105C0200E"], "a note broken over two lines stays two entries"
    assert community("010224")["notes"] == []


def test_names_keep_their_inner_spaces_and_numbers_their_character():
    ketchikan = community("020013")
    assert (ketchikan["community_name"], ketchikan["cid_suffix"], ketchikan["program"], ketchikan["current_map_date"], ketchikan["current_map_note"]) == ("KETCHIKAN,  CITY OF", None, None, None, None)
    assert {row["state"] for row in ROWS if row["cid"][:2] in nfip.TERRITORIES} == {"AS", "PR"}


@pytest.mark.parametrize("old, new, message", [
    ('"CID","Community Name"', '"Community ID","Community Name"', "the report's header is"),
    ('"Tribal","Sanction Date"', '"Tribal","Suspension Date"', "CID 'CID' is not six digits"),
    ('"Regular","NO"', '"Regular","NO","X"', "has 15 values, not 14"),
])
def test_a_header_or_line_the_build_does_not_know_stops_it(old, new, message):
    text = NFIP_CSV.decode("utf-8")
    data = text.replace(old, new, 1).encode("utf-8")
    assert data != NFIP_CSV
    with pytest.raises(SourceChanged, match=message):
        nfip.rows(data)


def test_values_that_disagree_with_the_part_they_are_in_stop_the_build():
    addison = line("ADDISON")
    with pytest.raises(SourceChanged, match=r"record 2: Participating Community is 'NO' in the part for communities participating"):
        nfip.rows(edited(addison, addison.replace('"YES"', '"NO"')))
    with pytest.raises(SourceChanged, match=r"note \(S\) on the date of entry"):
        nfip.rows(edited("(E)", "(S)"))
    with pytest.raises(SourceChanged, match="Tribal is 'Maybe'"):
        nfip.rows(edited(addison, addison.replace('"No"', '"Maybe"')))
    with pytest.raises(SourceChanged, match="Program is 'Special'"):
        nfip.rows(edited(addison, addison.replace('"Regular"', '"Special"')))


@pytest.mark.parametrize("old, new, message", [
    ('="010116B"', '="990116B"', "CID 990116 begins with 99, no state's or territory's FIPS code"),
    ('="010116B"', '="01011B"', "CID '01011B' is not six digits and a letter or #"),
    ('="010224#"', '="010116#"', "CID 010116 is listed twice"),
    ('="01/02/50"', '="13/01/50"', "Curr Eff Map Date is '13/01/50', not a date"),
    ('"12/10/76"', '"12/10/76(S)"', "with a note this column does not take"),
    ('"25%"', '"25"', "% Disc is '25'"),
])
def test_numbers_and_dates_the_build_cannot_read_stop_it(old, new, message):
    with pytest.raises(SourceChanged, match=re.escape(message)):
        nfip.rows(edited(old, new))


def test_a_line_without_a_number_before_any_community_or_with_other_values_stops_the_build():
    lines = NFIP_CSV.decode("utf-8").rstrip("\n").split("\n")
    with pytest.raises(SourceChanged, match="record 2: a line without a community number before any community"):
        nfip.rows("\n".join([lines[0], ',"A NOTE"' + "," * 12] + lines[1:]).encode("utf-8"))
    for line in (',"A NOTE","A COUNTY"' + "," * 11, ',"A NOTE",,="01/01/99"' + "," * 10, ',,"A COUNTY",,,,,,,,"5 "' + "," * 3):
        with pytest.raises(SourceChanged, match="neither a note nor the rest of a county list"):
            nfip.rows("\n".join(lines[:2] + [line] + lines[2:]).encode("utf-8"))
    cut = next(index for index, each in enumerate(lines) if "Sanction Date" in each)
    with pytest.raises(SourceChanged, match="no part for communities not participating"):
        nfip.rows("\n".join(lines[:cut]).encode("utf-8"))
    with pytest.raises(SourceChanged, match="the participating communities' header again"):
        nfip.rows("\n".join(lines + [lines[0]]).encode("utf-8"))
    with pytest.raises(SourceChanged, match="a second header for communities not participating"):
        nfip.rows("\n".join(lines + [lines[cut]]).encode("utf-8"))


def test_the_report_reconciles_with_openfemas_copy():
    reconciled = nfip.reconcile(ROWS, NFIP_API)
    assert reconciled == {"rows": 28, "in_report": 25, "only_in_api": {"participating": 1, "not_participating": 2},
                          "differ": {"crs_discount": [["350045", None, 5]], "initial_firm_date": [["025009", "1969-06-25", "2069-06-25"]]}}


@pytest.mark.parametrize("edit, message", [
    (lambda items: items.remove(next(item for item in items if item["communityIdNumber"] == "010116")), r"1 communities in the report are not in the OpenFEMA file: \['010116'\]"),
    (lambda items: items.append(dict(items[0])), "the OpenFEMA file lists 1 communities more than once"),
    (lambda items: next(item for item in items if item["communityIdNumber"] == "010116").update(communityName="BIRMINGHAM, TOWN OF"), r"disagree on \{'community_name': \[\['010116', 'BIRMINGHAM, CITY OF', 'BIRMINGHAM, TOWN OF'\]\]\}"),
    (lambda items: next(item for item in items if item["communityIdNumber"] == "010214").update(participatingInNFIP=True), "disagree on {'participating'"),
    (lambda items: next(item for item in items if item["communityIdNumber"] == "600001").update(state="HI"), "disagree on {'state'"),
])
def test_a_community_openfema_does_not_hold_alike_stops_the_build(edit, message):
    with pytest.raises(SourceChanged, match=message):
        nfip.reconcile(ROWS, api_edited(edit))


def test_other_differences_are_counted_not_stopped_on():
    data = api_edited(lambda items: next(item for item in items if item["communityIdNumber"] == "010116").update(county="JEFFERSON COUNTY", classRating="6"))
    differ = nfip.reconcile(ROWS, data)["differ"]
    assert differ["county"] == [["010116", "SHELBY COUNTY/JEFFERSON COUNTY", "JEFFERSON COUNTY"]] and differ["crs_class"] == [["010116", 5, 6]]


def test_a_file_that_is_not_parquet_stops_the_build():
    with pytest.raises(SourceChanged, match="cannot be read as parquet"):
        nfip.reconcile(ROWS, b"<html>")


def test_harvest_reads_both_files_and_a_snapshot_saves_and_loads_them(tmp_path, monkeypatch):
    clock = iter(["2026-09-26T08:18:00Z", "2026-09-26T08:23:00Z"])
    monkeypatch.setattr(nfip, "now", lambda: next(clock))
    fetcher = FakeFetcher()
    snapshot = nfip.harvest(fetcher)
    assert snapshot == nfip_snapshot() and fetcher.requests == 2
    nfip.save(snapshot, tmp_path / "nfip.zip")
    assert nfip.load(tmp_path / "nfip.zip") == snapshot


def test_a_snapshot_is_the_same_bytes_for_the_same_reading_and_loads_from_them(tmp_path):
    data = nfip.snapshot_bytes(nfip_snapshot())
    nfip.save(nfip_snapshot(), tmp_path / "nfip.zip")
    assert (tmp_path / "nfip.zip").read_bytes() == data == nfip.snapshot_bytes(nfip_snapshot())
    assert nfip.load(data) == nfip.load(tmp_path / "nfip.zip") == nfip_snapshot()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert [(info.filename, info.compress_type, info.date_time, info.create_system, info.external_attr >> 16) for info in archive.infolist()] == [
            (name, zipfile.ZIP_STORED, (1980, 1, 1, 0, 0, 0), 3, 0o644) for name in ("nation.csv", "NfipCommunityStatusBook.parquet", "read.json")]
    assert nfip.snapshot_bytes(nfip_snapshot() | {"retrieved_at": "2026-09-27T08:18:00Z"}) != data, "the reading's times are in the bytes"


def test_differs_names_what_is_not_the_reading_a_manifest_records():
    snapshot = nfip_snapshot()
    source = nfip.source(snapshot, ROWS, nfip.reconcile(ROWS, NFIP_API))
    assert nfip.differs(snapshot, source) == []
    assert nfip.differs(snapshot | {"csv": NFIP_CSV + b"\n", "api_retrieved_at": "2026-09-27T08:23:00Z"}, source) == ["api_retrieved_at", "nation.csv"]
    assert nfip.differs(snapshot | {"api": NFIP_API + b"\n", "retrieved_at": "2026-09-27T08:18:00Z"}, source) == ["NfipCommunityStatusBook.parquet", "retrieved_at"]
    assert nfip.differs(snapshot, {}) == ["NfipCommunityStatusBook.parquet", "api_retrieved_at", "nation.csv", "retrieved_at"]


def test_the_source_records_the_reading_and_the_dates_after_it():
    source = nfip.source(nfip_snapshot(), ROWS, nfip.reconcile(ROWS, NFIP_API))
    assert (source["rows"], source["participating"], source["with_notes"], source["bytes"]) == (25, 20, 10, len(NFIP_CSV))
    assert source["api"]["rows"] == 28 and source["api"]["retrieved_at"] == "2026-09-26T08:23:00Z" and source["api"]["name"] == nfip.API_NAME
    assert ["380146", "WILLIAMS COUNTY*", "current_map_date", "2050-01-02", None] in source["after_retrieval"]
    assert ["160056", "NEZ PERCE TRIBE", "sanction_date", "2027-10-29", None] in source["after_retrieval"]
    assert all(value > "2026-09-26" for *_, value, _ in source["after_retrieval"])


def test_stats_count_what_the_card_shows():
    stats = nfip.stats(ROWS)
    assert (stats["rows"], stats["participating"], stats["not_participating"], stats["tribal"], stats["tribal_participating"], stats["with_notes"]) == (25, 20, 5, 3, 1, 10)
    assert stats["programs"] == {"Regular": 18, "Emergency": 1, "none": 1}
    assert stats["status_notes"] == {"E": 1, "S": 2, "W": 1} and stats["map_notes"] == {">": 1, "All Zone D": 1, "L": 1, "M": 2, "NSFHA": 1}
    assert stats["crs_classes"] == {"1": {"communities": 1, "discounts": [45]}, "5": {"communities": 1, "discounts": [25]}, "7": {"communities": 2, "discounts": [15]}, "10": {"communities": 1, "discounts": []}}
