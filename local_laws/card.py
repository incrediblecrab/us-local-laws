"""Renders the dataset card (README.md on the Hub) from the manifest alone, so it is committed with every manifest and never disagrees with it."""

import datetime
from collections import Counter

from . import GITHUB, REPO_ID, census, locus, nfip, nyindex, nylaws, precision, tribes
from .schema import TABLES

TITLE = "US Local Governments and Ordinance Coverage"
LOCUS_URL = f"https://huggingface.co/datasets/{locus.REPO_ID}"
LOCUS_PAPER = "https://arxiv.org/abs/2606.19334"
LOCUS_CITATION = f'Denis Peskoff, Joe Barrow, Christopher Vu and Diag Davenport, "Freeing the Law with LOCUS: A Local Ordinance Corpus for the United States", [arXiv:2606.19334]({LOCUS_PAPER}) (2026)'
TYPE_NAMES = {"county": "County", "municipal": "Municipal", "township": "Township", "special_district": "Special district", "school_district": "Independent school district"}
TECH_DOC = "https://www2.census.gov/programs-surveys/gus/datasets/2022/2022_gov_org_meth_tech_doc.pdf"
HOWARD_ESTIMATES = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv"
ICC_RELEASE = "https://www.iccsafe.org/about/periodicals-and-newsroom/industry-leaders-american-legal-publishing-and-general-code-unite-to-provide-communities-with-best-in-class-codification-services/"
NY_FILING = "https://dos.ny.gov/local-law-filing"
NY_MANUAL = "https://dos.ny.gov/system/files/documents/2018/09/local-laws-website-manual.pdf"
NY_GUIDE = "https://dos.ny.gov/system/files/documents/2026/02/adopting-local-laws-in-nys.pdf"
NY_FORM = "https://dos.ny.gov/system/files/documents/2025/04/0239-f_0.pdf"
NY_DISCLAIMER = "https://dos.ny.gov/disclaimer"
# Read by paging through the API's filings filed in 2020 on September 26, 2026, when the whole API counted 147,844 assets and the category 147,795.
NY_OUTSIDE = "On September 26, 2026 the API also held 49 filings outside the search's Local Laws category, which the search does not show and which are not rows: filed from December 23 to 29, 2020 and all added on October 16, 2024, among them New York City's local laws 120 to 125 of 2020 and Suffolk County's 51 to 56. One, the Town of Stony Creek's local law 3 of 2020, is also in the category, filed again under a shorter title; for the other 48, no filing in the category from 2020 or 2021 has the same government, number and a like title."
CFR_TITLES = "https://www.govinfo.gov/content/pkg/CFR-2025-title37-vol1/xml/CFR-2025-title37-vol1-sec202-1.xml"
USC_105 = "https://www.copyright.gov/title17/92chap1.html#105"
NY_TITLES = {"TOWN OF": "Town", "VILLAGE OF": "Village", "CITY OF": "City", "COUNTY OF": "County"}


def size_category(rows):
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"), (1_000_000, "100K<n<1M")):
        if rows < limit:
            return label
    return "1M<n<10M"


def share(part, whole):
    return f"{part / whole:.1%}" if whole else "n/a"


def day(value):
    """September 26, 2026 for 2026-09-26 or 2026-09-26T04:27:00Z."""
    date = datetime.date.fromisoformat(value[:10])
    return f"{date:%B} {date.day}, {date.year}"


def moment(value):
    return f"{day(value)}, {value[11:16]} UTC"


def source_readings(manifest):
    sources = (manifest or {}).get("sources", {})
    return {"ny": sources.get("ny_local_laws", {}).get("finished_at"), "nfip": sources.get("nfip_communities", {}).get("retrieved_at")}


def front_matter(total):
    lines = ["---", f"pretty_name: {TITLE}", "license: mit", "language:", "- en",
             "tags:", "- legal", "- government", "- local-government", "- ordinances", "- municipal-codes", "- local-laws", "- new-york", "- census", "- tribes", "- flood-insurance", "- fema", "- united-states",
             "size_categories:", f"- {size_category(total)}", "configs:"]
    for index, (name, spec) in enumerate(TABLES.items()):
        lines += [f"- config_name: {name}", "  data_files:", "  - split: train", f"    path: {spec['file']}"]
        if index == 0:
            lines.append("  default: true")
    return lines + ["---", ""]


def coverage(stats):
    types, lx = stats["types"], stats["locus"]
    municipal, county, township = types["municipal"], types["county"], types["township"]
    years = Counter()
    for kind in locus.GENERAL_PURPOSE:
        years.update(types[kind]["population_years"])
    year, n = max(years.items(), key=lambda item: (item[1], item[0])) if years else ("none", 0)
    general = sum(years.values())
    lines = [
        "## Coverage",
        "",
        f"LOCUS has {lx['jurisdictions']:,} jurisdictions, and {lx['matched']:,} of them match one government here: {municipal['in_locus']:,} of the {municipal['governments']:,} municipal governments ({share(municipal['in_locus'], municipal['governments'])}), which hold {share(municipal['in_locus_population'], municipal['population'])} of the population the Census file gives for municipal governments; {county['in_locus']:,} of the {county['governments']:,} county governments ({share(county['in_locus'], county['governments'])}), holding {share(county['in_locus_population'], county['population'])} of county population; and {township['in_locus']:,} of the {township['governments']:,} township governments ({share(township['in_locus'], township['governments'])}).",
        "",
        "| Type | Governments | Active | Web address reported | In LOCUS | Population | Share of population in LOCUS |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for kind in census.TYPES:
        entry = types[kind]
        population = f"{entry['population']:,}" if "population" in entry else ""
        covered = share(entry["in_locus_population"], entry["population"]) if "population" in entry else ""
        lines.append(f"| {TYPE_NAMES[kind]} | {entry['governments']:,} | {entry['active']:,} | {entry['with_web_address']:,} | {entry['in_locus']:,} | {population} | {covered} |")
    totals = {key: sum(types[kind][key] for kind in census.TYPES) for key in ("governments", "active", "with_web_address", "in_locus")}
    lines += [
        f"| All | {totals['governments']:,} | {totals['active']:,} | {totals['with_web_address']:,} | {totals['in_locus']:,} | | |",
        "",
        f"Population is the Census Bureau's estimate for each county, municipal and township government, for {year} for {n:,} of the {general:,} ({share(n, general)}). Shares compare governments of one type only, and populations are never added across types, because the areas overlap: a municipality usually lies within a county, and in some states within a township too. LOCUS covers counties, municipalities and townships; special districts and independent school districts are here because the Census counts them as local governments. Active counts governments the Census does not mark dormant.",
        "",
    ]
    return lines


def use():
    base = f"hf://datasets/{REPO_ID}/data"
    return [
        "## Use",
        "",
        "```python",
        "from datasets import load_dataset",
        "",
        f'governments = load_dataset("{REPO_ID}", "governments", split="train")',
        f'crosswalk = load_dataset("{REPO_ID}", "locus_crosswalk", split="train")',
        f'ny_laws = load_dataset("{REPO_ID}", "ny_local_laws", split="train")',
        f'ny_index = load_dataset("{REPO_ID}", "ny_local_law_index", split="train")',
        f'tribes = load_dataset("{REPO_ID}", "federally_recognized_tribes", split="train")',
        f'flood = load_dataset("{REPO_ID}", "nfip_communities", split="train")',
        "```",
        "",
        "```sql",
        "-- DuckDB, straight from the Hub: Vermont's cities and towns, largest first, with the rows LOCUS holds for each",
        "SELECT g.name, g.government_type, g.county_name, g.population, c.locus_rows",
        f"FROM '{base}/governments.parquet' AS g",
        f"LEFT JOIN '{base}/locus_crosswalk.parquet' AS c USING (census_id)",
        "WHERE g.state = 'VT' AND g.government_type IN ('municipal', 'township')",
        "ORDER BY g.population DESC NULLS LAST;",
        "```",
        "",
        "```sql",
        "-- The ordinance text is in LOCUS (CC BY-NC 4.0). Find a government's LOCUS name here, then read its sections there, at the revision the crosswalk was built from.",
        f"SELECT locus_state, locus_jurisdiction_type, locus_name FROM '{base}/locus_crosswalk.parquet' WHERE census_id = '135964';",
        f"SELECT header, topic FROM 'hf://datasets/{locus.REPO_ID}@{locus.REVISION}/data/*.parquet' WHERE state = 'vt' AND source_jurisdiction_type = 'cities' AND city = 'barre' LIMIT 20;",
        "```",
        "",
        "```sql",
        "-- The New York governments that filed the most local laws in 2025",
        "SELECT g.name, g.county_name, count(*) AS filings",
        f"FROM '{base}/ny_local_laws.parquet' AS l",
        f"JOIN '{base}/governments.parquet' AS g USING (census_id)",
        "WHERE year(l.date_filed) = 2025",
        "GROUP BY ALL ORDER BY filings DESC LIMIT 20;",
        "",
        "-- Every local law the Town of Hempstead has filed, newest first, with the link to each filed PDF",
        f"SELECT date_filed, law_year, law_number, title, share_url FROM '{base}/ny_local_laws.parquet' WHERE census_id = '170895' ORDER BY date_filed DESC;",
        "",
        "-- Its oldest local laws in the State's older index",
        f"SELECT law_year, law_number, date_filed, title FROM '{base}/ny_local_law_index.parquet' WHERE census_id = '170895' ORDER BY date_filed, index_row LIMIT 20;",
        "```",
        "",
        "```sql",
        "-- The federally recognized Tribes in Alaska, in the Bureau of Indian Affairs' order",
        f"SELECT list_row, name, entry FROM '{base}/federally_recognized_tribes.parquet' WHERE list = 'alaska' ORDER BY list_row;",
        "",
        "-- The entries that are new, or changed other than in spaces and capitals, since the Bureau's previous list",
        f"SELECT name, previous_entry FROM '{base}/federally_recognized_tribes.parquet' WHERE previous_entry IS NULL OR replace(lower(previous_entry), ' ', '') <> replace(lower(entry), ' ', '') ORDER BY list_row;",
        "```",
        "",
        "```sql",
        "-- The communities in Louisiana that do not participate in the National Flood Insurance Program, with the date of each one's sanction",
        f"SELECT cid, community_name, county, sanction_date, status_note FROM '{base}/nfip_communities.parquet' WHERE state = 'LA' AND NOT participating ORDER BY community_name;",
        "",
        "-- Participating communities by state, and how many have a class in the Community Rating System",
        f"SELECT state, count(*) FILTER (WHERE participating) AS participating, count(*) FILTER (WHERE NOT participating) AS not_participating, count(crs_class) AS in_crs FROM '{base}/nfip_communities.parquet' GROUP BY state ORDER BY state;",
        "```",
        "",
    ]


def files(manifest):
    entries = manifest["files"]
    governments, crosswalk, ny, index, recognized, flood = (entries[TABLES[name]["file"]] for name in ("governments", "locus_crosswalk", "ny_local_laws", "ny_local_law_index", "federally_recognized_tribes", "nfip_communities"))
    return [
        "## Files",
        "",
        f"- `{TABLES['governments']['file']}`: one row per government, {governments['rows']:,} rows, sorted by `census_id`.",
        f"- `{TABLES['locus_crosswalk']['file']}`: one row per LOCUS jurisdiction, {crosswalk['rows']:,} rows, sorted by state, type and name.",
        f"- `{TABLES['ny_local_laws']['file']}`: one row per filing in New York's local-law search, {ny['rows']:,} rows, sorted by `filename`.",
        f"- `{TABLES['ny_local_law_index']['file']}`: one row per record in New York's older index of local laws, {index['rows']:,} rows, sorted by `index_row`.",
        f"- `{TABLES['federally_recognized_tribes']['file']}`: one row per entry of the Bureau of Indian Affairs' list of federally recognized Tribes, {recognized['rows']:,} rows, sorted by `list_row`.",
        f"- `{TABLES['nfip_communities']['file']}`: one row per community in FEMA's Community Status Book, {flood['rows']:,} rows, sorted by `report_row`.",
    ] + ([f"- `{nfip.SNAPSHOT}`: the two files the build read from FEMA, the report `nation.csv` and OpenFEMA's `NfipCommunityStatusBook.parquet`, stored uncompressed with `read.json`, which records when each was read. A run that cannot read fema.gov builds `nfip_communities` from them, and `python -m local_laws run --nfip-snapshot` takes the same file."] if nfip.SNAPSHOT in entries else []) + [
        "- `manifest.json`: each source's URL and SHA-256 or commit (for New York's API and FEMA's report, when each was read, with the counts New York's API gave then and the OpenFEMA check's results), each file's rows and SHA-256, every normalization the build applied with its count, the counts on this card, and the pipeline version and commit that built them.",
        "",
        "## Schema",
        "",
    ] + [line for name, spec in TABLES.items() for line in schema_table(name, spec)]


def schema_table(name, spec):
    lines = [f"### `{name}`", "", "| Column | Type | Description |", "|---|---|---|"]
    lines += [f"| `{column.name}` | {column.type} | {spec['docs'][column.name]} |" for column in spec["schema"]]
    return lines + [""]


def matching(manifest):
    lx = manifest["stats"]["locus"]
    matches = lx["matches"]
    matched = lx["matched"]
    fits = sum(entry["text_fits"] for entry in matches.values())
    differs = lx["text_differs"]
    unchecked = sum(entry["text_unchecked"] for name, entry in matches.items() if name in locus.MATCHED)
    lines = [
        "## Matching",
        "",
        f"LOCUS identifies a jurisdiction by state and a lowercase name, such as `batavia_city` or `saint_tammany_parish`, with no Census or FIPS code. The build matches each name to the Census file's county, municipal and township governments with the rules below, tried in this order. It then reads all {lx['rows']:,} rows of LOCUS's text and counts how the text writes each candidate's name with a type word, such as \"Town of Barre\" or \"Barre City\". Where several governments share a name, that count chooses among them (`text_type`, `text_county`); for every match it is a check (`text_fits`).",
        "",
        "| `match` | Rule | Jurisdictions | LOCUS rows | Text fits | Text differs | Not checked |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, doc in locus.MATCH_DOCS.items():
        entry = matches[name]
        lines.append(f"| `{name}` | {doc} | {entry['jurisdictions']:,} | {entry['rows']:,} | {entry['text_fits']:,} | {entry['text_differs']:,} | {entry['text_unchecked']:,} |")
    lines += ["", f"The text fits the matched government's Census title for {fits:,} of the {matched:,} matches ({share(fits, matched)})."]
    reviewed = locus.REVIEWED_DIFFERS if manifest["sources"]["locus"]["revision"] == locus.REVISION else frozenset()
    unread = sorted(set(differs) - reviewed)
    if differs and not unread:
        lines[-1] += f" It differs for {len(differs):,}. Each of those was read by hand against LOCUS's text on September 25, 2026, and each is the government LOCUS means, named with another word" + (f": {locus.REVIEW_FINDING}." if set(differs) == reviewed else ".")
    elif differs:
        lines[-1] += f" It differs for {len(differs):,}, and {len(unread):,} of those {'has' if len(unread) == 1 else 'have'} not been read by hand: {', '.join(f'`{name}`' for name in unread)}."
    silent = lx["matched_without_mentions"]
    few = lx["matched_few_mentions"]
    untitled = unchecked - few
    reasons = f"because it writes the name with a type word fewer than {locus.MIN_MENTIONS} times" if not untitled else f"{few:,} because it writes the name with a type word fewer than {locus.MIN_MENTIONS} times and {untitled:,} because the government's Census title has no type word to check"
    lines += [
        "",
        f"The text does not check {unchecked:,} matches, {reasons}. It never names {len(silent):,} of them that way: {', '.join(f'`{name}`' for name in silent) or 'none'}. That happens when the text does not use the Census name, as with consolidated governments such as METROPOLITAN GOVERNMENT OF NASHVILLE-DAVIDSON COUNTY, or when the text may not be about the place LOCUS files it under.",
        "",
        f"The {len(locus.ALIASES)} aliases, each checked by hand against the Census file and LOCUS's text. The build stops if the Census name at an alias's `census_id` changes or LOCUS drops the jurisdiction.",
        "",
        "| LOCUS jurisdiction | Census government |",
        "|---|---|",
    ]
    lines += [f"| `{state}/{name}` | {census_name} (`{census_id}`) |" for (state, _, name), (census_id, census_name) in locus.ALIASES.items()]
    return lines + [""]


def new_york(manifest):
    ny, source = manifest["stats"]["ny"], manifest["sources"]["ny_local_laws"]
    matches, kinds = ny["matches"], ny["municipality_types"]
    matched = sum(matches[name]["filings"] for name in nylaws.MATCHED)
    early = sum(entry["filings"] for year, entry in ny["years"].items() if int(year) < 1998)
    titled = sum(entry["with_title"] for entry in ny["years"].values())
    numbered = sum(entry["with_law_year"] for entry in ny["years"].values())
    lines = [
        "## New York local laws",
        "",
        f"New York's counties, cities, towns and villages each adopt local laws, and the Department of State's [Local Law Filing]({NY_FILING}) page, under Municipal Home Rule Law §27, says: \"Each local law shall be filed with the Secretary of State within 20 days after its final adoption or approval. A local law does not become effective until it is filed in the Office of the Secretary of State.\" The Department publishes the filings in its [Local Laws search]({source['app']}), whose [manual]({NY_MANUAL}) says the database holds local laws \"on or after January 1, 1998 by counties, cities, towns and villages\" and \"county codes filed on or after April 1, 2015 by counties\".",
        "",
        f"`ny_local_laws` holds every filing the search's API returned from {moment(source['started_at'])} to {moment(source['finished_at'])}: {ny['filings']:,} filings, filed from {day(ny['first_filed'])} to {day(ny['last_filed'])}. The build reads the category one filing year at a time and stops unless each year's rows are as many as the API counts for that year and the whole is what it counts before and after the reading; `verify` checks the published table against those counts, recorded in `manifest.json`. {codifications(ny['codifications'])}, and {early:,} {'carries' if early == 1 else 'carry'} a filing date before 1998.",
        "",
        f"The metadata is the Department's. {titled:,} filings ({share(titled, ny['filings'])}) have a title and {numbered:,} ({share(numbered, ny['filings'])}) a law year; the rest name the government, the law's number and the filing date. The Department says the number it assigns a local law \"may be different from the number ascribed by the legislative body of the local government\" ([Local Law Filing]({NY_FILING})).{misdated(ny['filed_after_posted'])}",
        "",
        *posting(ny["posting"]),
        "The filed PDFs are not read: the hosts that serve them tell robots not to (`Disallow: /` in their robots.txt), so each row keeps the public link the search page gives for its PDF, and the law's text stays there.",
        "",
        "| Filed in | Filings | With a title | With a law year | Matched to a government | Repeating another |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for year, entry in ny["years"].items():
        lines.append(f"| {year} | {entry['filings']:,} | {entry['with_title']:,} | {entry['with_law_year']:,} | {entry['matched']:,} | {entry['repeats']:,} |")
    lines += [
        "",
        repeated(ny),
        "",
        f"Each filing names its government by the Department's type (`municipality_type`) and name (`municipality_name`). The build matches the pair to one of New York's county, municipal and township governments in `governments`, only ever to a government with the Census title the type gives (TOWN OF for Town), so a filing is never matched to a same-name government of another type. {matched:,} of the {ny['filings']:,} filings ({share(matched, ny['filings'])}) are matched.",
        "",
        checked(),
        "",
        "| `match` | Rule | Filings | Distinct names |",
        "|---|---|---:|---:|",
    ]
    for name, doc in nylaws.MATCH_DOCS.items():
        lines.append(f"| `{name}` | {doc} | {matches[name]['filings']:,} | {matches[name]['names']:,} |")
    lines += ["", "| Department type | Filings | Matched | Share matched |", "|---|---:|---:|---:|"]
    for kind, entry in kinds.items():
        lines.append(f"| {kind or '(none)'} | {entry['filings']:,} | {entry['matched']:,} | {share(entry['matched'], entry['filings'])} |")
    lines += ["", "| Census title | Governments | With a matched filing | Share |", "|---|---:|---:|---:|"]
    for title, entry in ny["governments"].items():
        lines.append(f"| {title} | {entry['governments']:,} | {entry['with_filings']:,} | {share(entry['with_filings'], entry['governments'])} |")
    lines += ["", without_filings(ny["without_filings"])]
    lines += [
        "",
        f"Ambiguous, with the number of filings: {listed(ny['ambiguous'])}.",
        "",
        f"Unmatched, with the number of filings: {listed(ny['unmatched'])}.",
        "",
    ]
    return lines


def ny_index(manifest):
    index, source = manifest["stats"]["ny_index"], manifest["sources"]["ny_local_law_index"]
    matches, rows = index["matches"], index["rows"]
    matched = sum(matches[name]["rows"] for name in nylaws.MATCHED)
    lines = [
        "## New York's index of older local laws",
        "",
        f"The Department's search begins in 1998, and its [manual]({NY_MANUAL}) says \"Local Laws filed prior to January 1, 1998 are not in the Local Laws Database. They are maintained in manual files.\" For older laws there is an index: under the Freedom of Information Law, the State released a copy of an index database of local laws filed with the Secretary of State, made with DataPerfect, on March 17, 2022 (FOIL Request No. DOS-22-02-052). The Local Geohistory Project published the release, with a tab-separated export of it, as [\"{nyindex.TITLE}\"](https://doi.org/{source['doi']}) ({nyindex.CREATOR}, v1.0.0), whose [README]({source['repository']}) describes \"index records for over 130,000 local laws filed with the Secretary of State, mostly between 1969 and 2003\".",
        "",
        f"`ny_local_law_index` is that export, {rows:,} rows, read from the release at the SHA-256 the build pins. The export is the project's own, made by running DataPerfect over the State's files and tidying its output, so the build checks it against the State's data file in the same release, whose header counts {source['state_records']:,} records: each record is one of the export's rows and each row one record, alike in the eight fields other than the title, with the State's text read as the export writes it, runs of spaces as one and ¨ as a. The State keeps the titles in another file, which the build does not read, so they are as the export gives them.",
        "",
        f"Law years run from {index['first_law_year']} to {index['last_law_year']}. {rows_were(index['filed_before_search'])} filed before 1998, when the search begins; of the {index['filed_from_search']:,} filed from 1998 on, {index['in_search']:,} ({share(index['in_search'], index['filed_from_search'])}) {'has' if index['in_search'] == 1 else 'have'} a row in `ny_local_laws` with the same type, name, law number and filing date. The index gives filing dates from {day(index['first_filed'])} to {day(index['last_filed'])}, and {index['undated']:,} {'row has' if index['undated'] == 1 else 'rows have'} none.{index_misdated(index)} Entry dates run from {day(index['first_entered'])} to {day(index['last_entered'])}, and {rows - index['with_title']:,} {'row has' if rows - index['with_title'] == 1 else 'rows have'} no title.",
        "",
        "| Law year | Rows | Matched to a government |",
        "|---|---:|---:|",
    ]
    for decade, entry in index["law_years"].items():
        lines.append(f"| {'(none)' if decade == 'none' else decade} | {entry['rows']:,} | {entry['matched']:,} |")
    lines += [
        "",
        f"Rows are matched to governments by the rules under New York local laws, reading the type with only its first letter capitalized, since the index sometimes writes it otherwise, as TOWN or town. {matched:,} of the {rows:,} rows ({share(matched, rows)}) are matched.",
        "",
        "| `match` | Rows | Distinct names |",
        "|---|---:|---:|",
    ]
    for name in nylaws.MATCHES:
        lines.append(f"| `{name}` | {matches[name]['rows']:,} | {matches[name]['names']:,} |")
    lines += ["", "| Type as the index writes it | Rows | Matched |", "|---|---:|---:|"]
    for kind, entry in sorted(index["municipality_types"].items(), key=lambda item: (-item[1]["rows"], item[0])):
        lines.append(f"| {kind or '(none)'} | {entry['rows']:,} | {entry['matched']:,} |")
    lines += ["", "| Census title | Governments | With a matched row | Share |", "|---|---:|---:|---:|"]
    for title, entry in index["governments"].items():
        lines.append(f"| {title} | {entry['governments']:,} | {entry['with_rows']:,} | {share(entry['with_rows'], entry['governments'])} |")
    lines += [
        "",
        f"Ambiguous, with the number of rows: {listed(index['ambiguous'])}.",
        "",
        f"Unmatched, with the number of rows: {listed(index['unmatched'])}.",
        "",
    ]
    return lines


def rows_were(n):
    return f"{n:,} {'row was' if n == 1 else 'rows were'}"


def index_misdated(index):
    """A sentence, with its leading space, on the index's filing dates that disagree with its other fields; empty when there are none."""
    before, after = index["filed_before_law_year"], index["filed_after_entry"]
    if not before and not after:
        return ""
    return f" {before:,} {'row has' if before == 1 else 'rows have'} a filing date before the law's own year, so one of the two is wrong, and {after:,} a filing date after the day the record was entered; the table keeps the dates as recorded."


def recognized_tribes(manifest):
    table, source = manifest["stats"]["tribes"], manifest["sources"]["federally_recognized_tribes"]
    lists, added, changed = table["lists"], table["added"], table["changed"]
    says = f" {tribes.NOTICE_SAYS}" if source["document_number"] == tribes.NOTICE["document_number"] else ""
    respaced_names = table["respaced_names"]
    same = f", {table['respaced']:,} of them only when spaces and capitals are ignored, {f'{len(respaced_names):,}' if respaced_names else 'none'} of those in the name before any parenthesis" if table["respaced"] else ""
    return [
        "## Federally recognized Tribes",
        "",
        f"The Census of Governments does not count tribal governments. Under the Federally Recognized Indian Tribe List Act of 1994, the Bureau of Indian Affairs publishes in the Federal Register the list of the Tribal entities it recognizes. `federally_recognized_tribes` is the list in its notice of {day(source['published'])}, [{source['citation']}]({source['page']}), read from the notice's XML at the SHA-256 the build pins: one row per entry, {table['rows']:,} in all, {lists['contiguous_48']:,} in its list for the contiguous 48 states and {lists['alaska']:,} in its list for Alaska, in the notice's order. Each row is a name as the Bureau lists it, with the former names and notes the notice gives in parentheses, and nothing more: no lands, members, offices or laws.",
        "",
        stated_count(table, source),
        "",
        f"The build checks the list against the notice it says it updates, of {day(source['previous_published'])} ([{source['previous_citation']}]({source['previous_page']})), which had {source['previous_entries']:,} entries: each of them must be continued by exactly one row, as the same entry, as the one the row's \"previously listed as\" names, or with its parentheticals dropped, compared without spaces or capitals; and the rows that continue none must be as many as the stated count grew by. {table['unchanged']:,} entries are the same{same}; {len(changed):,} changed, {f"{table['renamed']:,}" if table['renamed'] else 'none'} of them in the name before any parenthesis; and {f"{len(added):,} {'is' if len(added) == 1 else 'are'} new: {', '.join(added)}" if added else 'none is new'}. `previous_entry` holds each row's earlier entry.{says}",
        "",
        f"Changed since the earlier notice: {', '.join(changed) or 'none'}.",
        "",
        *respaced(respaced_names),
    ]


def flood_communities(manifest):
    table, source = manifest["stats"]["nfip"], manifest["sources"]["nfip_communities"]
    api, programs = source["api"], table["programs"]
    only = api["only_in_api"]
    unset = f"; the report gives no program for {programs['none']:,}" if programs["none"] else ""
    return [
        "## Flood insurance communities",
        "",
        f"FEMA's page for its [Community Status Book]({source['page']}) says: \"{nfip.PROGRAM_SAYS}\" `nfip_communities` is the book's national report, [nation.csv]({source['url']}), as fema.gov served it on {moment(source['retrieved_at'])}: one row per community, {table['rows']:,} in all, {table['participating']:,} participating in the program and {table['not_participating']:,} not, in the report's order. Of those participating, {programs['Regular']:,} are in its Regular Program and {programs['Emergency']:,} in its Emergency Program{unset}. The report marks {table['tribal']:,} communities as tribal, {table['tribal_participating']:,} of them participating. Each row gives the dates of the community's first flood maps and of its current map, the date it entered the program or was sanctioned, and its class in the Community Rating System, with the notes the report prints beneath {table['with_notes']:,} communities. The table records which communities have joined and when, not the regulations they adopted: that text is not here, and the communities are not matched to `governments`.",
        "",
        f"The build checks the report against the book as FEMA's OpenFEMA API publishes it, the dataset [{api['name']}]({api['page']}), read from `{api['url']}` on {moment(api['retrieved_at'])}: every community in the report must be in it once, under the same name and state and with the same participation, or the build stops. The API holds {api['rows']:,} communities, {only['participating'] + only['not_participating']:,} more than the report lists, {only['participating']:,} of them participating and {only['not_participating']:,} not; they are not rows. {api_agreement(api['differ'])}",
        "",
        f"The Community Rating System, the page says, is \"{nfip.CRS_SAYS}\". {table['crs']:,} communities have a class in it, with the discount on flood insurance premiums the report gives for each class:",
        "",
        "| Class | Communities | Discount |",
        "|---:|---:|---:|",
        *(f"| {n} | {entry['communities']:,} | {', '.join(f'{value}%' for value in entry['discounts']) or 'none'} |" for n, entry in sorted(table["crs_classes"].items(), key=lambda item: int(item[0]))),
        "",
        *later_dates(source),
    ]


def api_agreement(differ):
    """The sentence on the report's values that the OpenFEMA file gives otherwise."""
    if not differ:
        return "The report's other values, its counties, tribal marks, dates, codes and classes, agree with the API's for every community."

    def shown(value):
        return "null" if value is None else str(value)

    parts = []
    for field, cases in differ.items():
        listed = "; ".join(f"{cid}, {shown(ours)} here and {shown(theirs)} in the API" for cid, ours, theirs in cases[:5])
        parts.append(f"`{field}` for {len(cases):,} {'community' if len(cases) == 1 else 'communities'} ({listed}{'; ...' if len(cases) > 5 else ''})")
    return f"The report's other values, its counties, tribal marks, dates, codes and classes, agree with the API's for every community except {' and '.join(parts)}."


def later_dates(source):
    """The paragraph on the dates later than the day the report was read, if any."""
    after = source["after_retrieval"]
    if not after:
        return []
    read = datetime.date.fromisoformat(source["retrieved_at"][:10])
    columns = Counter(column for _, _, column, _, _ in after)
    marked = sum(1 for *_, note in after if note == ">")
    far = [(cid, name, column, value) for cid, name, column, value, _ in after if datetime.date.fromisoformat(value).year > read.year + 5]
    distant = "; ".join(f"{name} ({cid}), whose `{column}` reads as {day(value)}" for cid, name, column, value in far)
    return [
        f"{len(after):,} dates are later than the day the report was read: " + ", ".join(f"{count:,} in `{column}`" for column, count in columns.most_common()) + f". The report marks {marked:,} of the current map dates as after the date of the report (`>`); the rest it gives without comment."
        + (f" {len(far):,} {'is' if len(far) == 1 else 'are'} more than five years ahead: {distant}. The report writes years with two digits, which the table reads as 1968 to 2067." if far else ""),
        "",
    ]


def respaced(names):
    """The sentence on names that differ from the earlier notice's only in spaces or capitals, if any."""
    if not names:
        return []
    listed = "; ".join(f"{current}, which the earlier notice typed {earlier}" for current, earlier in names)
    return [f"Names that differ from the earlier notice's only in spaces or capitals, kept as each notice types them: {listed}.", ""]


def stated_count(table, source):
    """The paragraph on the count the notice states, beside the entries its lists hold."""
    rows, stated = table["rows"], source["stated"]
    said = f"The notice's summary says it publishes \"the current list of {stated} Tribal entities\""
    if rows == stated:
        return f"{said}, as many as its lists hold."
    gap = rows - stated
    text = f"{said}, {abs(gap):,} {'fewer' if gap > 0 else 'more'} than the {rows:,} entries its lists hold, and the notice it updates stated {source['previous_stated']:,} for {source['previous_entries']:,} entries. Neither says why."
    referred = table["referred"]
    if referred:
        named = "; ".join(f"{' and '.join(others)} to {target}" for target, others in referred.items())
        text += f" Some entries send the reader to another with \"See\": {named}."
        if rows - len(referred) == stated:
            text += f" The stated count is the entries less the {len(referred):,} that others send the reader to, but the notice does not say that is how the Bureau counts, so every entry is a row."
    return text


def repeat_years(ny):
    """(repeats by filing year, the one or two years with the most, and how much of the whole those hold: all, most or the largest shares)."""
    years = {year: entry["repeats"] for year, entry in ny["years"].items() if entry["repeats"]}
    top = [year for year, _ in sorted(years.items(), key=lambda item: (-item[1], item[0]))[:2]]
    held, total = sum(years[year] for year in top), sum(years.values())
    return years, top, "all" if held == total else "most" if 2 * held > total else "the largest shares"


def repeated(ny):
    """The sentence on rows that are the same filing as another by type, name, law number and filing date."""
    years, top, portion = repeat_years(ny)
    if not years:
        return "No two rows have the same type, name, law number and filing date."
    named = " and ".join(f"{year} ({years[year]:,} of its {ny['years'][year]['filings']:,} rows)" for year in top)
    return f"Repeating another counts, of each set of rows with the same type, name, law number and filing date, the rows after the first: {sum(years.values()):,} in all, {portion} in {named}. Each is a separate PDF in the library, and the table keeps each as a row; whether a repeat is a second copy of one filing or another law under the same number is not known here, since the PDFs are not read."


def listed(entries):
    return ", ".join(f"{kind or '(none)'} {name or '(none)'} ({n:,})" for kind, name, n in entries) or "none"


def titled(census_name):
    """Town of Hempstead for TOWN OF HEMPSTEAD."""
    return census_name.title().replace(" Of ", " of ")


def checked():
    """The paragraph on the check of New York's matches against the filings' text, from the answers precision.py keeps."""
    parts = precision.summary()
    main, second = parts["main"], parts["supplement"]
    n, found = main["filings"], main["outcomes"]
    confirmed_names = [census_name for part, _, _, _, census_name, generic, name, _, _, other in precision.RESULTS if part == "main" and precision.outcome(generic, name, other) == "confirmed"]
    example = f", such as {confirmed_names[0]}," if confirmed_names else ""
    contradicted = found["contradicted"]
    verdict = "No filing was" if not contradicted else f"{contradicted:,} {'was' if contradicted == 1 else 'were'}"
    lines = [
        f"To check the matches, {n} matched filings were drawn at random, leaving out five used first to try the queries, and looked up on {day(precision.CHECKED)} in the search API's full-text index of the filed PDFs; the PDFs themselves were not fetched. The API was asked about one filing at a time, once with that text and once without it, so a term found only with it is in the text and not in the filing's metadata.",
        f"{found['confirmed']:,} of the {n} were confirmed: their text has the matched government's Census name{example} and their metadata does not. {verdict} contradicted, that is, found to name a same-name government of another type and not the matched one.",
    ]
    rest = []
    if found["in_metadata"]:
        rest.append(f"for {found['in_metadata']:,}, the metadata has the name too, so the search cannot tell whether the text does")
    if found["not_named"]:
        rest.append(f"for {found['not_named']:,}, the text has neither the name nor that of a same-name government of another type")
    if found["no_text"]:
        rest.append(f"for {found['no_text']:,}, the search finds no text")
    if rest:
        lines.append("Of the rest, " + "; ".join(rest) + ".")
    lines.append(f"As controls, the Census name of another government of the same type, drawn at random, was found in {main['decoys_found']:,} of the {n} filings, and that of a same-name government of another type in {main['other_found']:,} of the {main['with_other']:,} filings that have one.")
    pairs = [(census_name, other_name) for part, _, _, _, census_name, _, _, _, _, other in precision.RESULTS if part == "supplement" for other_name, _, hit in other if hit]
    confirmed_second = second["outcomes"]["confirmed"]
    second_line = f"Of a second draw of {second['filings']:,} filings matched by name and county, {'all ' if confirmed_second == second['filings'] else ''}{confirmed_second:,} were confirmed"
    if second["other_found"]:
        second_line += f", and {second['other_found']:,} of the {second['with_other']:,} with a same-name government of another type have its Census name as well, as the {titled(pairs[0][0])}'s filing has {pairs[0][1]}"
    lines.append(second_line + ".")
    lines.append(form_titles(parts))
    filings = main["filings"] + second["filings"]
    with_text = main["with_text"] + second["with_text"]
    if with_text == filings:
        lines.append(f"Every filing checked, the oldest filed {day(min(main['first_filed'], second['first_filed']))}, has text the search reads: a term the check asked, such as \"hereby\" or \"enacted\", is in its text and not its metadata.")
    else:
        lines.append(f"{with_text:,} of the {filings:,} filings checked have text the search reads: a term the check asked, such as \"hereby\" or \"enacted\", is in their text and not their metadata.")
    lines.append(f"A sample of {n} cannot rule out wrong matches among several percent of filings. [`local_laws/precision.py`]({GITHUB}/blob/main/local_laws/precision.py) keeps the draw and every answer, and [`checks/ny_precision.py`]({GITHUB}/blob/main/checks/ny_precision.py) redraws it from the published tables.")
    return " ".join(lines)


def form_titles(parts):
    """The sentence on the matched name found after a title that no government of that name has."""
    asked = sum(entry["unnamed_asked"] for entry in parts.values())
    found = sum(entry["unnamed_found"] for entry in parts.values())
    phrases = [(census_name, phrase, hit) for _, filename, _, _, census_name, *_ in precision.RESULTS for phrase, hit in precision.unnamed(filename)]
    if not found:
        return f"The matched name after a title that no government of that name has, such as {phrases[0][1]} for the {titled(phrases[0][0])}, was found in none of the {asked:,} filings checked."
    census_name, phrase, _ = next(entry for entry in phrases if entry[2])
    return f"Yet the matched name after a title that no government of that name has, such as {phrase} for the {titled(census_name)}, was found in {found:,} of the {asked:,} filings checked, likely because the Department's [filing form]({NY_FORM}) prints the four titles before \"of\" and the government's name. So a phrase naming a same-name government of another type does not by itself make a match wrong, and a confirmation shows that the text has the name more surely than that it has the title."


def codifications(n):
    if n == 1:
        return "1 of the filings is a county codification rather than a local law"
    return f"{n:,} of the filings are county codifications rather than local laws"


def misdated(filings):
    """A sentence, with its leading space, on filings dated after the day they were added; empty when there are none."""
    if not filings:
        return ""
    named = "; ".join(f"{kind} {name}, local law {number or '(none)'}, filed {day(filed)} by its record and added {day(added)} (`{filename}`)" for filename, kind, name, number, filed, added in filings)
    if len(filings) == 1:
        return f" One filing is dated after the day it was added, so one of those dates is wrong; the table keeps both as the Department recorded them: {named}."
    return f" {len(filings):,} filings are dated after the day they were added, so one date of each is wrong; the table keeps the dates as the Department recorded them: {named}."


def posting(posted):
    lines = ["`posted_at` is when a filing was added to the Department's document library, which the search reads." + (f" {posted['moved']:,} filings were added from {day(posted['moved_from'])} to {day(posted['moved_to'])}, {posted['moved_filed_before']:,} of them filed before then; for those, `posted_at` is the day they came into the library and says nothing of when they were first published." if posted["moved"] else ""), ""]
    quote = f"The Department's [manual]({NY_MANUAL}), dated February 19, 2016, says filings are \"usually included in the Local Laws Database within two business days\"."
    if not posted["recent"]:
        return lines + [f"{quote} No filing was added in the {nylaws.RECENT_DAYS} days to {day(posted['latest'])} with a filing date before it was added, so the table has no recent measure of how long that takes.", ""]
    return lines + [f"{quote} Of the {posted['recent']:,} filings added in the {nylaws.RECENT_DAYS} days to {day(posted['latest'])}, {posted['within_two_weekdays']:,} ({share(posted['within_two_weekdays'], posted['recent'])}) were added within two weekdays after the day they were filed; at least half were added within {posted['median_weekdays']:,} {'weekday' if posted['median_weekdays'] == 1 else 'weekdays'}, and at least nine in ten within {posted['p90_days']:,} {'day' if posted['p90_days'] == 1 else 'days'}. Days are counted in New York time, and holidays count as weekdays, so a wait over a holiday counts a day more than it would in business days.", ""]


def without_filings(places):
    if not places:
        return "Every government in the table has at least one matched filing."
    named = "; ".join(f"{name}, in {county.title() + ' County' if county else 'no county'} (`{census_id}`{f', population {population:,}' if population is not None else ''})" for census_id, name, county, population in places)
    if len(places) == 1:
        return f"The one government in the table without a matched filing is {named}."
    return f"The {len(places):,} governments in the table without a matched filing are {named}."


def codifiers(manifest):
    lx = manifest["stats"]["locus"]
    return [
        "## Where local law is published",
        "",
        "The Law Library of Congress's guide to municipal codes says \"there is no one singular clearinghouse for all municipal codes\" ([Municipal Codes: A Beginner's Guide](https://guides.loc.gov/municipal-codes)). A few private codifiers publish many governments' codes online, for the governments that hire them:",
        "",
        "- Municode, owned by CivicPlus. When CivicPlus announced that it had acquired Municode, Municode hosted \"over 3,900 local government codes and 190,000 individual ordinances\" ([CivicPlus](https://www.civicplus.com/news/nn/civicplus-acquires-municode/)).",
        f"- ICC Code Solutions, the International Code Council's union of General Code (eCode360) and American Legal Publishing, which \"now supports more than 7,000 communities across 47 states, Canada and more than a dozen tribal territories\" ([ICC, July 16, 2026]({ICC_RELEASE})).",
        "",
        "This dataset does not collect from them, because their terms restrict automated collection. The [ICC's Terms of Use](https://www.iccsafe.org/about/terms-of-use/) (last revised May 4, 2023) name General Code and American Legal Publishing among the companies they cover and eCode 360 among their E-Content, and say in §4:",
        "",
        "> the licenses ICC grants to use our E-Content, as set forth in these Terms of Use and applicable E-Content Terms, do not include any: ... or use of data mining, robots, or similar data gathering and extraction tools with any Service or E-Content. Please contact us at license@iccsafe.org to discuss additional licenses for further uses.",
        "",
        "The [CivicPlus Terms of Use](https://www.civicplus.help/legal-center/docs/civicplus-terms-of-use) (last revised March 20, 2026) list among prohibited uses, in §7:",
        "",
        "> Use or launch any automated system, including without limitation, \"robots,\" \"spiders,\" or \"offline readers,\" that accesses the Site or any Solution in a manner that sends more request messages to the CivicPlus servers in a given period of time than a human can reasonably produce in the same period by using a conventional on-line web browser, unless specifically permitted by CivicPlus and in accordance with any specific rate or use limitations;",
        "",
        "Their §8(b) forbids anyone to \"engage in Automated Scraping of any CivicPlus Materials or Authority Data for AI Training Purposes\", and §8(a) defines Authority Data as materials provided \"by, for, or on behalf of any government entity\" through CivicPlus's services, \"whether such materials are publicly accessible or non-public\".",
        "",
        "On September 25, 2026, eCode360 and American Legal Publishing's code library also answered plain HTTP requests with a Cloudflare challenge (HTTP 403 with `cf-mitigated: challenge`); the pipeline treats a challenge as a stop, never as something to get past. Which codifier, if any, publishes a government's code is therefore not in this dataset.",
        "",
        f"LOCUS's paper describes a raw corpus of codes from 9,239 cities and counties, \"available for release to researchers\" ([arXiv:2606.19334]({LOCUS_PAPER})); the public LOCUS-v1 has {lx['jurisdictions']:,} jurisdictions.",
        "",
    ]


def gaps(manifest):
    stats = manifest["stats"]
    types, lx = stats["types"], stats["locus"]
    notes = manifest["sources"]["census_governments"]["normalization"]
    ny_source = manifest["sources"]["ny_local_laws"]
    dormant = sum(entry["governments"] - entry["active"] for entry in types.values())
    web = sum(entry["with_web_address"] for entry in types.values())
    lines = [
        "## Known gaps",
        "",
        "- Tribal governments are not rows. Besides the federal government and the 50 state governments, the Census Bureau \"recognizes five basic types of local governments\" ([technical documentation](" + TECH_DOC + ")), the five in `government_type`, and tribal governments are not among them. `federally_recognized_tribes` lists the Tribes the United States recognizes by name only: it does not match them to governments or places, holds none of their laws, and leaves out Tribes that a state recognizes and the United States does not. Nor are the territories' governments rows: the Census file covers the 50 states and the District of Columbia.",
        f"- Dependent public school systems, {notes.get('dependent_school_systems_skipped', 0):,} in the Census file, are left out, as the Census leaves them out of its count: they \"are classified as agencies of other state, county, municipal, or town or township governments and are not counted as separate governments\" ([technical documentation]({TECH_DOC})).",
    ]
    if "ks/howard" in lx["ambiguous"]:
        lines.append(f"- The Census file can omit an active government. Howard, Kansas, in Elk County, is an active incorporated city in the Census's own [Vintage 2024 population estimates]({HOWARD_ESTIMATES}) (561 people in 2024), and LOCUS has its code as `ks/howard`, but it is not in the 2022 file; so `ks/howard` is `ambiguous` between the two townships named Howard, neither of whose titles its text fits.")
    others = [name for name in lx["ambiguous"] if name != "ks/howard"] + lx["unmatched"]
    if others:
        lines.append(f"- {len(others):,} other LOCUS {'jurisdiction is' if len(others) == 1 else 'jurisdictions are'} `ambiguous` or `unmatched`: {', '.join(f'`{name}`' for name in others)}.")
    lines += [
        "- The Census of Governments is for 2022: a government formed, merged or dissolved since then is not reflected.",
        f"- {dormant:,} governments the Census marks dormant are rows, because the Census still counts them.",
        f"- Web addresses are as reported to the Census, which says it did minimal quality control on them; they are not checked here. {web:,} of the {stats['governments']:,} governments reported one. The Census file's mailing addresses and contact titles are left out: the map does not need them, and a few of the addresses name a person.",
        "- LOCUS's text is not here, and LOCUS names are matched to governments by name, with the precision described under Matching. Which governments' codes the codifiers hold is not known here; see Where local law is published.",
        f"- `ny_local_laws` covers New York alone, and only its local laws. The Department's guide, [Adopting Local Laws in New York State]({NY_GUIDE}), notes that under the Municipal Home Rule Law a local law \"shall not include an ordinance, resolution or other similar act of the legislative body\", so those acts are not here. Nor are laws filed before the Department's database begins: its manual says \"Local Laws filed prior to January 1, 1998 are not in the Local Laws Database. They are maintained in manual files.\" For those, `ny_local_law_index` has the State's older index, {stats['ny_index']['filed_before_search']:,} rows filed before 1998, without the laws' text.",
        f"- `ny_local_laws` is the search as it answered on {day(ny_source['finished_at'])}. A filing the Department had not yet added, or has removed or corrected since, is not reflected, and since filings took days to weeks to be added (see New York local laws), the weeks before then are incomplete.",
        f"- {NY_OUTSIDE}",
        *repeats_gap(stats["ny"]),
        scans(),
        tribes_gap(manifest["sources"]["federally_recognized_tribes"]),
        nfip_gap(manifest["sources"]["nfip_communities"]),
        "- `ny_local_law_index` is an index: it holds no law's text and no link to a filing, and its titles are not checked against the State's files (see New York's index of older local laws). Its names are as the index records them, some cut short or misspelled, and a village dissolved before 2022 has no government in the 2022 Census of Governments, so such rows are `unmatched`.",
        "- New York filings are matched to governments by the Department's type and name, and the county a name sometimes carries, not by their text; a check of a sample against the text is under New York local laws. A village dissolved before 2022 has no government in the 2022 Census of Governments, and a filing recorded under a name or type the Census does not use is `unmatched`; both are listed under New York local laws.",
        "",
    ]
    return lines


def tribes_gap(source):
    latest = f", the latest the Federal Register's API found on {day(tribes.LATEST_CHECKED)}" if source["document_number"] == tribes.NOTICE["document_number"] else ""
    return f"- `federally_recognized_tribes` is the Bureau's list as its notice of {day(source['published'])} gives it{latest}: a Tribe recognized, or an entry corrected, since then is not reflected."


def nfip_gap(source):
    only = source["api"]["only_in_api"]
    return f"- `nfip_communities` is FEMA's report as fema.gov served it on {day(source['retrieved_at'])}; FEMA regenerates it, so a community's standing may have changed since. It says which communities participate in the National Flood Insurance Program, not what their floodplain regulations say, and its names, such as ALAMOGORDO, CITY OF, are FEMA's and are not matched to `governments`. The {only['participating'] + only['not_participating']:,} communities that OpenFEMA's copy holds and the report does not list are not rows."


def repeats_gap(ny):
    years, top, portion = repeat_years(ny)
    if not years:
        return []
    total = sum(years.values())
    return [f"- {total:,} {'row' if total == 1 else 'rows'} of `ny_local_laws` {'has' if total == 1 else 'have'} the same type, name, law number and filing date as another row, {'filed in' if total == 1 else f'{portion} of them filed in'} {' and '.join(top)}; they may be second copies of the same filings, and are counted by year under New York local laws."]


def scans():
    """The gap bullet on the laws' text, with what the check of the matches found of it."""
    parts = precision.summary().values()
    filings = sum(entry["filings"] for entry in parts)
    with_text = sum(entry["with_text"] for entry in parts)
    first, last = min(entry["first_filed"] for entry in parts), max(entry["last_filed"] for entry in parts)
    return f"- The laws' text is not here, only a link to each filed PDF, which this pipeline does not open. The search reads text for {'every one' if with_text == filings else f'{with_text:,}'} of the {filings:,} filings the check of the matches looked up, filed from {day(first)} to {day(last)}, but that is a sample: whether every filing's text can be searched, and how faithful to the page the text of a scan is, is not known here."


def fema_refused(manifest):
    """What a run does when it cannot read fema.gov, and what verify checks FEMA's rows against: while the manifest lists no stored copy of FEMA's files, such a run on GitHub Actions writes nothing."""
    read = day(manifest["sources"]["nfip_communities"]["retrieved_at"])
    if nfip.SNAPSHOT not in manifest["files"]:
        return (f"On September 27, 2026, fema.gov returned HTTP 403 for `nation.csv` to a GitHub-hosted runner in both the cheap probe and the full sync path, so a scheduled run that cannot refresh FEMA logs a warning with the HTTP status and headers, writes no commit, and leaves the FEMA table at the last successful report date in this manifest, {read}. "
                "`python -m local_laws verify` re-reads the published files and checks them against `manifest.json`, the Census's table, LOCUS's card, the API's counts recorded at the reading, the index's release, the Bureau's notices and FEMA's report, row for row while fema.gov still serves the file the build read.")
    return (f"On September 27, 2026, fema.gov returned HTTP 403 for `nation.csv` to a GitHub-hosted runner in both the cheap probe and the full sync path, so each build stores the two files it read from FEMA in `{nfip.SNAPSHOT}`. A run that cannot read fema.gov logs a warning with the error, checks the stored files against their SHA-256s and reading times in this manifest, and builds `nfip_communities` from them, so New York's filings still update while the FEMA table stays at the report as read on {read}. When `{nfip.HARVEST}` holds a newer reading, which `python -m local_laws harvest-nfip --publish` stores from a machine fema.gov answers, such a run builds from it instead, with the same checks, and stores it in `{nfip.SNAPSHOT}`. "
            "`python -m local_laws verify` re-reads the published files and checks them against `manifest.json`, the Census's table, LOCUS's card, the API's counts recorded at the reading, the index's release, the Bureau's notices and FEMA's report, row for row against the stored copy and, while fema.gov still serves the file the build read, against fema.gov.")


def sources(manifest):
    census_source, org02, locus_source, ny, index, recognized, flood = (manifest["sources"][key] for key in ("census_governments", "census_org02", "locus", "ny_local_laws", "ny_local_law_index", "federally_recognized_tribes", "nfip_communities"))
    return [
        "## Sources",
        "",
        "| Source | Pinned as | Used for |",
        "|---|---|---|",
        f"| [2022 Census of Governments, Government Units List]({census_source['url']}) | SHA-256 `{census_source['sha256']}` | `governments`: {census_source['rows']:,} rows |",
        f"| [CG2200ORG02, Local Governments by Type and State, 2022]({org02['url']}) | SHA-256 `{org02['sha256']}` | Checking `governments`: {org02['counts_compared']:,} counts by state and type compared, {org02['mismatches']:,} differ |",
        f"| [LOCUS-v1]({LOCUS_URL}) | commit `{locus_source['revision']}` | `locus_crosswalk`: {locus_source['rows']:,} rows read |",
        f"| [New York Department of State, Local Laws search]({ny['app']}), through its API `{ny['api']}` | read from {moment(ny['started_at'])} to {moment(ny['finished_at'])}, when it counted {ny['total']:,} filings | `ny_local_laws` |",
        f"| [{nyindex.TITLE}](https://doi.org/{index['doi']}), v1.0.0, from the Local Geohistory Project | SHA-256 `{index['sha256']}` | `ny_local_law_index`: {index['rows']:,} rows, checked against the {index['state_records']:,} records of the State's data file in the release |",
        f"| Bureau of Indian Affairs, [\"{tribes.TITLE}\"]({recognized['page']}), {recognized['citation']}, {day(recognized['published'])} | SHA-256 `{recognized['sha256']}` of its XML | `federally_recognized_tribes`: {recognized['rows']:,} rows |",
        f"| The notice it updates, [{recognized['previous_citation']}]({recognized['previous_page']}), {day(recognized['previous_published'])} | SHA-256 `{recognized['previous_sha256']}` of its XML | Checking `federally_recognized_tribes`: its {recognized['previous_entries']:,} entries, each continued by one row |",
        f"| FEMA, [Community Status Book]({flood['page']}), its national report [nation.csv]({flood['url']}) | read on {moment(flood['retrieved_at'])}; SHA-256 `{flood['sha256']}` | `nfip_communities`: {flood['rows']:,} rows |",
        f"| FEMA, OpenFEMA dataset [{flood['api']['name']}]({flood['api']['page']}), `{flood['api']['url']}` | read on {moment(flood['api']['retrieved_at'])}; SHA-256 `{flood['api']['sha256']}` | Checking `nfip_communities`: its {flood['api']['rows']:,} records, every community in the report among them |",
        "",
        f"`python -m local_laws probe` cheaply compares New York's filing counts by year, FEMA's report hashes and this card with the published manifest. When a scheduled GitHub Actions run needs a build, `python -m local_laws run` downloads the Census files, LOCUS, New York's older index and the Bureau's two notices at their pinned versions and reads New York's API and FEMA's report afresh, or builds from snapshots of them that `python -m local_laws harvest-ny` and `harvest-nfip` wrote. It stops if any source's bytes or counts are not what the pipeline was checked against, and commits the tables{", the two FEMA files it read" if nfip.SNAPSHOT in manifest["files"] else ""}, `manifest.json` and this card in one commit. The Census files, LOCUS, the index and the notices are fixed releases; `python -m local_laws check-pins` fails a scheduled run when a newer LOCUS or Bureau notice needs review before its pin changes. {fema_refused(manifest)}",
        "",
    ]


def by_state(stats):
    lines = ["## By state", "", "| State | County | Municipal | Township | Special district | Independent school district | All | In LOCUS |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for state, entry in stats["states"].items():
        lines.append(f"| {state} | " + " | ".join(f"{entry[kind]:,}" for kind in census.TYPES) + f" | {sum(entry[kind] for kind in census.TYPES):,} | {entry['in_locus']:,} |")
    return lines + [""]


def license_section(manifest):
    tribe_source, flood = manifest["sources"]["federally_recognized_tribes"], manifest["sources"]["nfip_communities"]
    return [
        "## License",
        "",
        "MIT, for the compilation and the code that builds it.",
        "",
        f"- The Census files are works of the United States Government and are not subject to copyright in the United States ([17 U.S.C. § 105]({USC_105})).",
        f"- The crosswalk records facts about LOCUS-v1, namely its jurisdiction names, their row counts and counts of type words in their text, and holds none of its text. LOCUS licenses its text [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), which anyone using that text must follow. Cite LOCUS as {LOCUS_CITATION}.",
        f"- `ny_local_laws` records facts about each filing, namely who filed it, its number, its dates, its PDF's name, size and public link, and the law's title as the Department records it, and holds none of the laws' text. Titles are among the \"words and short phrases such as names, titles, and slogans\" that the Copyright Office's rules list as not subject to copyright ([37 C.F.R. § 202.1(a)]({CFR_TITLES})). On September 26, 2026 the Department's Local Laws pages linked no terms of use, its [disclaimer]({NY_DISCLAIMER}) covered only its links to other sites, and the API's host had no robots.txt (it answered HTTP 404).",
        f"- `ny_local_law_index` is the State's older index as the Local Geohistory Project published it, and the [Zenodo record]({nyindex.RECORD}) names its license \"Creative Commons Zero v1.0 Universal\" ([CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)). Its records are the State's: the release's [README]({nyindex.REPOSITORY}) says the DataPerfect files in it \"were created by the State of New York\". Like `ny_local_laws`, the table records facts about each filing, namely who filed it, the law's year and number, its dates and pages, and its title and subject as the State recorded them, and holds none of the laws' text. Cite it as {nyindex.CREATOR}, \"{nyindex.TITLE}\", v1.0.0, Local Geohistory Project, December 31, 2023, [doi:{nyindex.DOI}](https://doi.org/{nyindex.DOI}).",
        f"- `federally_recognized_tribes` is taken from notices the Bureau of Indian Affairs published in the Federal Register, each signed by its Assistant Secretary—Indian Affairs. They are works of the United States Government, and \"Copyright protection under this title is not available for any work of the United States Government\" ([17 U.S.C. § 105]({USC_105})). Cite the list as Bureau of Indian Affairs, \"{tribes.TITLE}\", {tribe_source['citation']} ({day(tribe_source['published'])}).",
        f"- `nfip_communities` is read from FEMA's Community Status Book on fema.gov, a work of the United States Government ([17 U.S.C. § 105]({USC_105})), and FEMA's [website information]({nfip.REUSE}) says: \"{nfip.REUSE_SAYS}\" Cite it as FEMA, National Flood Insurance Program Community Status Book, {flood['url']}, read on {moment(flood['retrieved_at'])}. The rows are the report's; the build only checks them against OpenFEMA's copy, read on {moment(flood['api']['retrieved_at'])} from `{flood['api']['url']}`, and OpenFEMA's [terms]({nfip.API_TERMS}) ask its users to state: \"{nfip.OPENFEMA_STATEMENT}\"" + (f" `{nfip.SNAPSHOT}` holds the report and OpenFEMA's file as the build read them, unchanged." if nfip.SNAPSHOT in manifest["files"] else ""),
        "",
    ]


def render(manifest):
    stats = manifest["stats"]
    ny = manifest["sources"]["ny_local_laws"]
    lines = front_matter(sum(manifest["files"][spec["file"]]["rows"] for spec in TABLES.values()))
    lines += [
        f"# {TITLE}",
        "",
        f"Every local government the Census Bureau's 2022 Census of Governments counts, {stats['governments']:,} of them in the 50 states and the District of Columbia; which of them have ordinance text in [LOCUS]({LOCUS_URL}), a public corpus of local ordinances; and every local law in the New York Department of State's online search, {stats['ny']['filings']:,} filings, with the State's older index of local laws, {stats['ny_index']['rows']:,} rows, each matched where it can be to the government that filed it. It maps who makes local law, how much of that law LOCUS holds, and, for New York, the local laws its governments have filed with the state since 1998 and, in the older index, with law years back to {stats['ny_index']['first_law_year']}; the laws' text is not here. Beside them, `federally_recognized_tribes` lists the {stats['tribes']['rows']:,} entries of the Bureau of Indian Affairs' list of federally recognized Tribes, whose governments the Census does not count among local governments, and `nfip_communities` the {stats['nfip']['rows']:,} communities in FEMA's Community Status Book, {stats['nfip']['participating']:,} of which participate in the National Flood Insurance Program, whose communities agree to adopt floodplain management regulations.",
        "",
        f"Every row is built by code: the Census files, LOCUS and New York's older index from versions pinned by SHA-256 or commit, which the build checks against each other, and the index against the State's own file in its release; the Bureau's list from its notice in the Federal Register, pinned by SHA-256 and checked against the notice it updates; New York's filings from the Department's search API as it answered on {day(ny['finished_at'])}, checked against the API's own counts; and the flood insurance communities from FEMA's report as fema.gov served it on {day(manifest['sources']['nfip_communities']['retrieved_at'])}, checked against OpenFEMA's copy of the book. The only hand-made input to the rows is {len(locus.ALIASES)} name aliases for LOCUS, listed under Matching. The pipeline and its tests are in [{GITHUB.removeprefix('https://')}]({GITHUB}), and this card is rendered from `manifest.json` in the same commit.",
        "",
    ]
    readings = source_readings(manifest)
    lines += [
        "## Source freshness",
        "",
        "| Source | Published reading |",
        "|---|---|",
        f"| New York local-law filings | {moment(readings['ny'])} |",
        f"| FEMA Community Status Book | {moment(readings['nfip'])} |",
        "",
        "A successful scheduled run does not prove FEMA is current. If fema.gov cannot be read, the run reports degraded freshness and retains the published FEMA reading while New York updates can continue. These dates describe the stored data, not the latest probe; the Actions summary reports which sources that probe could check.",
        "",
    ]
    lines += coverage(stats) + new_york(manifest) + ny_index(manifest) + recognized_tribes(manifest) + flood_communities(manifest) + use() + files(manifest) + matching(manifest) + codifiers(manifest) + gaps(manifest) + sources(manifest) + license_section(manifest) + by_state(stats)
    return "\n".join(lines)
