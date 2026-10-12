# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and GitHub Copilot CLI when working in this repository.

This repository builds and publishes the public Hugging Face dataset `incrediblecrab/us-local-laws`: every US local government in the 2022 Census of Governments, crosswalked to LOCUS-v1 ordinance text, New York's local-law filings, BIA-recognized Tribes and FEMA's Community Status Book. The repository is public. `README.md` gives sources, terms and the run schedule; `local_laws/README.md`, `tests/README.md` and `checks/README.md` describe their folders.

## Commands

```bash
.venv/bin/python -m pytest -q                         # offline suite, what test.yml runs on push
.venv/bin/python -m pytest -q tests/test_nfip.py -k <name>
.venv/bin/python tests/mutate.py                      # disables each check in a copy; fails unless the suite catches it
.venv/bin/python -m local_laws {probe,run,verify,card,check-pins,check-freshness,harvest-ny,harvest-nfip} --help
```

`.venv` links to the shared `~/.venvs/main`. A local build that touches nothing published uses `--local DIR` and snapshots in `scratch/` (the README's "Try it" line has the exact sequence). `run` without `--local` publishes to the Hub from a clean commit, so do not run it, or `harvest-nfip --publish`, unless the owner asks. `checks/` holds hand-run network checks whose results are recorded in the dataset card; they are not part of the build.

## How it fits together

`cli.py` dispatches to `build.py`, which downloads, checks and assembles the six tables, then `store.py` writes the tables, the FEMA files read, manifest and card as one commit, guarded by a parent-commit fence. Each source has its own module (`census.py`, `locus.py`, `nylaws.py`, `nyindex.py`, `tribes.py`, `nfip.py`); `schema.py` documents columns, `card.py` renders the card and `verify.py` re-reads and compares what was published. `http.py` owns pacing (one New York request a second, 15 seconds between fema.gov requests), retries and stopping on bot challenges; keep every network call going through it.

Census files, LOCUS, the New York index and the BIA notices are pinned by SHA-256 or commit and change only when a person reviews a new release; `check-pins` fails loudly when a newer one appears. New York and FEMA are live snapshots. fema.gov refuses GitHub-hosted runners, so FEMA data refreshes only from a `harvest-nfip --publish` run on a machine fema.gov answers (a launchd job on the owner's Mac does this daily), and the scheduled build otherwise reuses the copy stored in the dataset. `MAX_UNVERIFIED_HOURS` in `local_laws/nfip.py` sets when stale FEMA data fails the freshness check.

## Constraints

LOCUS-v1 is CC BY-NC: publish only facts derived from it (names, counts), never its text, and keep test fixtures synthetic (`tests/conftest.py` writes a fake LOCUS for this reason). Respect each source's robots.txt and pacing as recorded in the README's "Sources and terms". Dependencies in `pyproject.toml` are exact pins, matching the published build. Publishing from GitHub Actions uses Trusted Publishing with no stored token; locally it needs `HF_TOKEN` from the keychain. GitHub disables schedules after 60 days without a commit; the workflow's `inactivity` job fails at 50 days to warn first and deliberately makes no keepalive commit, which GitHub treats as a Terms violation, so do not add one.
