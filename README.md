# us-local-laws

Builds [incrediblecrab/us-local-laws](https://huggingface.co/datasets/incrediblecrab/us-local-laws), a public Hugging Face dataset of the local governments of the United States, with a crosswalk to their ordinance text and, for New York, every local law filed with the State. [What it contains](#what-it-contains) describes the sources, and the dataset card gives the counts, the matching rules and the known gaps.

**Objective:** map who makes local law in the United States and how much of that law is openly published, using only sources whose terms allow automated use.

**Inputs:** the Census's 2022 Government Units List and table CG2200ORG02; LOCUS-v1; the New York Department of State's filing records and the State's older index of local laws; the Bureau of Indian Affairs' two latest Federal Register notices of federally recognized Tribes; and FEMA's Community Status Book report. [Sources and terms](#sources-and-terms) gives the terms each is used under and how the build reads it.

**Files:**

- [`.github/workflows/`](.github/workflows/README.md): the scheduled GitHub Actions workflow
- [`local_laws/`](local_laws/README.md): the pipeline package
- [`tests/`](tests/README.md): offline tests
- [`checks/`](checks/README.md): one-off checks run by hand against a build, whose results the card states
- [`pyproject.toml`](pyproject.toml): pinned dependencies

**Try it:** `pip install .`, then `mkdir -p scratch`, `python -m local_laws harvest-ny --out scratch/ny.json.gz`, `python -m local_laws harvest-nfip --out scratch/nfip.zip`, `python -m local_laws run --local scratch/out --workdir scratch/work --ny-snapshot scratch/ny.json.gz --nfip-snapshot scratch/nfip.zip` and `python -m local_laws verify --local scratch/out`. Without `--local`, `run` publishes from a clean commit; locally it may use `HF_TOKEN`, while GitHub Actions uses Hugging Face Trusted Publishing with no stored token. [Running a build](#running-a-build) gives what each step downloads.

## What it contains

The dataset covers every local government in the Census Bureau's 2022 Census of Governments, with a crosswalk to the ordinance text in [LOCUS-v1](https://huggingface.co/datasets/LocalLaws/LOCUS-v1) and, for New York, every local law in the Department of State's [Local Laws search](https://dos.ny.gov/local-laws) and the State's older index of local laws, released under FOIL and [published by the Local Geohistory Project](https://doi.org/10.5281/zenodo.10446245), each matched where it can be to the government that filed it. Beside them it lists the Tribes the United States recognizes, from the Bureau of Indian Affairs' latest notice in the Federal Register, since the Census does not count tribal governments, and the communities in FEMA's [Community Status Book](https://www.fema.gov/flood-insurance/work-with-nfip/community-status-book), which records which communities participate in the National Flood Insurance Program, whose communities agree to adopt floodplain management regulations. The dataset card gives the counts, the matching rules and the known gaps.

## Sources and terms

- The Census's 2022 Government Units List and table CG2200ORG02 (public domain).
- LOCUS-v1 (CC BY-NC 4.0), of which only facts are published: jurisdiction names, row counts and type-word counts.
- The filing records the New York Department of State's search reads from its API, whose robots.txt is absent: names, numbers, dates, titles and share links, not the filed PDFs, whose host's robots.txt disallows all robots.
- The Local Geohistory Project's release of New York's older index (CC0 1.0).
- The XML of the Bureau of Indian Affairs' two latest notices of its list of federally recognized Tribes, works of the United States Government and not subject to copyright, read at the full-text address the Federal Register's API gives for each, since the site limits programmatic access to its API; its robots.txt allows that path.
- FEMA's Community Status Book report, `nation.csv`, a work of the United States Government, checked against OpenFEMA's copy of the book, whose terms the card quotes; fema.gov's robots.txt allows both paths and asks for 15 seconds between requests, which the build keeps.

The Census files, LOCUS, the index release and the two notices are pinned by SHA-256 or commit; New York's records and FEMA's report are each read once into a snapshot, checked, New York's against the API's own counts and FEMA's against OpenFEMA's copy, and built from.

## Running a build

GitHub Actions runs at 00:00 and 12:00 UTC. Each scheduled run first probes with about 38 New York count requests and 2 FEMA downloads; if those match the published manifest and the card render still matches, it skips the full build. When a build is needed, the New York harvest makes about 1,500 requests to New York's API, one a second; the FEMA harvest makes 2 to fema.gov, 15 seconds apart, for 3.1 MB of report and 0.96 MB of OpenFEMA parquet; `run` without `--ny-snapshot` or `--nfip-snapshot` makes them itself. A run downloads 1.77 GB of LOCUS parquet to a work directory it then deletes, the 10 MB index release from Zenodo and the Bureau's two notices, about 100 KB, from the Federal Register; `verify` downloads the index release, the notices, CG2200ORG02 and FEMA's report again, to compare; it compares the report row for row only while fema.gov still serves the file the build read, since FEMA regenerates it, and always compares the rows with the copy of the report the build stored, which needs no network. The Census files, LOCUS, the index and the notices stay pinned until a person reviews a new release; the scheduled pin check fails loudly with an error naming the newer release, after any New York/FEMA update that can still be built from the reviewed pins is published. As measured on September 27, 2026, fema.gov returned HTTP 403 for the Community Status Book CSV to GitHub-hosted runners in the probe and full sync path. Each build therefore stores the two FEMA files it read in the dataset, as `sources/nfip_snapshot.zip`, which the manifest lists with its SHA-256. A run that cannot read fema.gov warns, checks the stored files against the manifest's SHA-256s and reading times, and builds the FEMA table from them, so New York's filings keep updating. The FEMA table refreshes only when a build runs where fema.gov answers; on GitHub-hosted runners it stays at the stored reading, whose date the card states. Before any build has stored the files, a refused run on GitHub Actions warns and writes no commit.

**Reading run status:** the probe reports New York and FEMA separately, including the reading dates from the published manifest. A refused FEMA request gives `status: degraded` and says its freshness is unverified, even when New York is unchanged and no build is needed. It does not block a New York update. The dataset card puts those stored reading dates under Source freshness; they are not the time of the latest probe. A green workflow alone does not establish that FEMA is current, and this reporting change does not remove fema.gov's refusal. The FEMA parser skips full-width CSV records containing only whitespace, while retaining the original record numbers and continuing to reject malformed records and disagreements with OpenFEMA.

**Freshness alert:** `python -m local_laws check-freshness` runs after publishing, verification and the pin check, even when a previous check failed or the probe skipped the build. It compares both published FEMA file hashes with a fresh read. Matching hashes establish that the stored files are current without changing their recorded reading dates; differing hashes fail the check. If FEMA cannot be read, the check warns while both stored readings are younger than `MAX_UNVERIFIED_HOURS` in `local_laws/nfip.py`, and fails once either reaches that limit. Missing, invalid or future reading dates also fail. The report appears in the Actions summary as `freshness`. This final alert does not roll back or prevent New York updates, and does not bypass FEMA's access restrictions.

## License

MIT. See [`LICENSE`](LICENSE).
