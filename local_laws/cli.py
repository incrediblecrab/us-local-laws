"""python -m local_laws {run,verify,card,harvest-ny,harvest-nfip}: build the dataset and publish it, check what is published, re-render its card, or read New York's local-law filings or FEMA's Community Status Book into a snapshot to build from."""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

from . import REPO_ID, locus, nfip, nylaws, tribes
from .build import build, code_version, unchanged
from .card import render, source_readings
from .census import SourceChanged
from huggingface_hub.errors import HfHubHTTPError
from .http import Blocked, Fetcher, Unavailable
from .store import CARD, MANIFEST, HubStore, LocalStore, Superseded

# Exit codes besides 0 (done) and 1 (verify found problems, a commit was superseded or a publisher/pin check failed).
STOPPED = 2
# What the Hub answers when Trusted Publishing was not registered for this repository and workflow.
NO_PUBLISHER = "No trusted publisher configured"

def open_store(args, write=False):
    """Reads are anonymous (token=False): the dataset is public. A write uses the token huggingface_hub finds, such as HF_TOKEN or Trusted Publishing, and creates the repo if it does not exist."""
    if args.local:
        return LocalStore(args.local)
    if write and os.environ.get("GITHUB_ACTIONS") == "true":
        os.environ.setdefault("HF_OIDC_RESOURCE", f"datasets/{args.repo}")
    return HubStore(args.repo, token=None if write else False, create=write)

def github_output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")

def warn(message, level="warning"):
    print(f"::{level}::{message}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"{level}: {message}")

def trusted_publisher_error(error, repo):
    if NO_PUBLISHER not in str(error):
        return False
    warn(f"{NO_PUBLISHER} for {repo}, so nothing was written. Register repository incrediblecrab/us-local-laws, branch main and workflow pipeline.yml under the dataset's Settings > Trusted Publishers.", level="error")
    github_output(commits=0)
    return True


def http_status(error):
    return getattr(getattr(error, "response", None), "status_code", None)


def fema_http_error(error):
    request = getattr(error, "request", None)
    url = str(getattr(request, "url", ""))
    return url.startswith(nfip.CSV_URL) or url.startswith(nfip.API_URL)


def response_headers(error):
    headers = getattr(getattr(error, "response", None), "headers", {})
    keep = ("date", "server", "content-type", "content-length", "cf-mitigated", "x-cache", "via")
    return {key: headers[key] for key in keep if key in headers}


def last_nfip_fetch(store):
    try:
        text = store.read_text(MANIFEST)
    except Exception:  # noqa: BLE001 - this is diagnostic only
        return None
    if not text:
        return None
    return (json.loads(text).get("sources", {}).get("nfip_communities", {}).get("retrieved_at"))


def ci_fema_warning(error, store):
    if os.environ.get("GITHUB_ACTIONS") != "true" or not isinstance(error, httpx.HTTPStatusError) or not fema_http_error(error):
        return False
    request = getattr(error, "request", None)
    url = str(getattr(request, "url", nfip.CSV_URL))
    last = last_nfip_fetch(store) or "unknown"
    warn(f"FEMA Community Status Book refresh from GitHub Actions failed: GET {url} returned HTTP {http_status(error)} with headers {response_headers(error)}. Last successful FEMA fetch in the published manifest: {last}. No commit was written; the next schedule will try again.")
    github_output(commits=0)
    return True


def stored_nfip_snapshot(store):
    """The FEMA files the published build read, from the snapshot it stored, once they are shown to be the reading its manifest records; None if it stored none."""
    text = store.read_text(MANIFEST)
    manifest = json.loads(text) if text else {}
    entry = (manifest.get("files") or {}).get(nfip.SNAPSHOT)
    if entry is None:
        return None
    data = store.read_bytes(nfip.SNAPSHOT)
    if data is None or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise SourceChanged(f"{nfip.SNAPSHOT} is not the file the manifest lists")
    snapshot = nfip.load(data)
    differ = nfip.differs(snapshot, (manifest.get("sources") or {}).get("nfip_communities") or {})
    if differ:
        raise SourceChanged(f"{nfip.SNAPSHOT} is not the reading the manifest records: its {differ} differ")
    return snapshot


def read_fema(fetcher, store):
    """FEMA's two files as fema.gov serves them now. When fema.gov refuses, as it refused GitHub-hosted runners on September 27, 2026, the files the published build read and stored, so that New York's filings still update and the FEMA table stays as published; with no stored files the error propagates, and nothing is written."""
    try:
        return nfip.harvest(fetcher)
    except (Blocked, Unavailable, httpx.HTTPStatusError) as error:
        stored = stored_nfip_snapshot(store)
        if stored is None:
            raise
        refused = f"GET {error.request.url} returned HTTP {http_status(error)} with headers {response_headers(error)}" if isinstance(error, httpx.HTTPStatusError) else f"{type(error).__name__}: {error}"
        warn(f"FEMA's Community Status Book could not be read: {refused}. Building nfip_communities from {nfip.SNAPSHOT}, FEMA's files as read on {stored['retrieved_at']}.")
        return stored

def publishable(args):
    """The code version to record, or None when writing to the Hub from code that is not committed: a published build names the commit that made it."""
    code = code_version()
    if not args.local and (code["commit"] is None or code["dirty"] is not False):
        print("refusing to write to the Hub from a working tree with uncommitted changes (or outside git): commit first, so the manifest names the code that built it", file=sys.stderr)
        return None
    return code

def cmd_run(args):
    code = publishable(args)
    if code is None:
        return STOPPED
    try:
        store = open_store(args, write=True)
    except HfHubHTTPError as error:
        if trusted_publisher_error(error, args.repo):
            return 1
        raise
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="local-laws-"))
    workdir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher()
    try:
        snapshot = nylaws.load(args.ny_snapshot) if args.ny_snapshot else None
        flood = nfip.load(args.nfip_snapshot) if args.nfip_snapshot else read_fema(fetcher, store)
        manifest, files = build(fetcher, workdir, ny_snapshot=snapshot, nfip_snapshot=flood, code=code)
        stats = manifest["stats"]
        summary = {"governments": stats["governments"], "locus_jurisdictions": stats["locus"]["jurisdictions"], "matched": stats["locus"]["matched"],
                   "ny_filings": stats["ny"]["filings"], "ny_matched": sum(stats["ny"]["matches"][name]["filings"] for name in nylaws.MATCHED),
                   "ny_index_rows": stats["ny_index"]["rows"], "ny_index_matched": sum(stats["ny_index"]["matches"][name]["rows"] for name in nylaws.MATCHED),
                   "tribes": stats["tribes"]["rows"], "nfip_communities": stats["nfip"]["rows"], "requests": fetcher.requests}
        if unchanged(store, manifest):
            print(json.dumps(dict(summary, commit=None, unchanged=True), indent=1))
            github_output(commits=0)
            return 0
        read = manifest["sources"]["ny_local_laws"]["finished_at"][:10]
        flood_read = manifest["sources"]["nfip_communities"]["retrieved_at"][:10]
        message = f"Build from the 2022 Census of Governments, LOCUS-v1 {locus.REVISION[:12]}, New York's local laws as of {read} and FEMA's Community Status Book as of {flood_read}" + (f" (pipeline {code['commit'][:12]})" if code["commit"] else "")
        try:
            oid = store.commit({repo_path: str(local) for repo_path, local in files.items()}, message)
        except HfHubHTTPError as error:
            if trusted_publisher_error(error, args.repo):
                return 1
            raise
        print(json.dumps(dict(summary, commit=oid, unchanged=False), indent=1))
        github_output(commits=1)
        return 0
    except (SourceChanged, Blocked, Unavailable, httpx.HTTPStatusError) as error:
        if ci_fema_warning(error, store):
            return 0
        print(f"stopped, nothing written: {type(error).__name__}: {error}", file=sys.stderr)
        return STOPPED
    except Superseded as error:
        print(f"not written: {error}", file=sys.stderr)
        return 1
    finally:
        fetcher.close()
        if not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)

def ny_head(fetcher):
    """Cheap New York state: category total plus the count in each filing year, the same totals harvest reconciles after reading every page."""
    before = nylaws.total(fetcher, nylaws.category())
    first = nylaws.first_filed(fetcher, "dateFiled").year
    last = nylaws.first_filed(fetcher, "-dateFiled").year
    years = {}
    for year in range(first, last + 1):
        years[str(year)] = nylaws.total(fetcher, nylaws.category(nylaws.filed_between(*nylaws.year_span(year))))
    after = nylaws.total(fetcher, nylaws.category())
    return {"total": after, "years": years, "stable": before == after == sum(years.values())}

def nfip_head(fetcher):
    """Cheap FEMA state: hashes of the two files harvest would snapshot, without parsing the whole build."""
    csv = fetcher.get(nfip.CSV_URL)
    api = fetcher.get(nfip.API_URL)
    return {"sha256": hashlib.sha256(csv).hexdigest(), "bytes": len(csv), "api_sha256": hashlib.sha256(api).hexdigest(), "api_bytes": len(api)}

def probe_decision(manifest, card, head):
    reasons = []
    readings = source_readings(manifest)
    status = {"ny": "not_published", "nfip": "not_published" if head.get("nfip") is not None else "unavailable"}
    if manifest is None:
        reasons.append("no manifest is published")
    else:
        sources = manifest.get("sources", {})
        ny = sources.get("ny_local_laws", {})
        if ny.get("total") != head["ny"]["total"] or ny.get("years") != head["ny"]["years"]:
            reasons.append("New York local-law counts changed")
        status["ny"] = "changed" if reasons or not head["ny"].get("stable") else "unchanged"
        if not head["ny"].get("stable"):
            reasons.append("New York local-law counts changed while probing")
        flood = sources.get("nfip_communities", {})
        api = flood.get("api", {})
        if head.get("nfip") is None:
            status["nfip"] = "unavailable"
        elif flood.get("sha256") != head["nfip"]["sha256"] or api.get("sha256") != head["nfip"]["api_sha256"]:
            reasons.append("FEMA Community Status Book changed")
            status["nfip"] = "changed"
        else:
            status["nfip"] = "unchanged"
        if card != render(manifest):
            reasons.append("dataset card render changed")
    degraded = head.get("nfip") is None
    return {"needed": bool(reasons), "reasons": reasons or ["checked sources match; FEMA freshness is unverified" if degraded else "published sources match"],
            "status": "degraded" if degraded else "update_needed" if reasons else "current",
            "sources": {name: {"status": status[name], "retrieved_at": readings[name]} for name in readings}}

def cmd_probe(args):
    fetcher = Fetcher()
    try:
        store = open_store(args)
        text = store.read_text(MANIFEST)
        manifest = json.loads(text) if text else None
        card = store.read_text(CARD) if manifest else None
        head = {"ny": ny_head(fetcher)}
        try:
            head["nfip"] = nfip_head(fetcher)
        except (Blocked, Unavailable, httpx.HTTPStatusError, httpx.TransportError) as error:
            head["nfip"] = None
            last = source_readings(manifest)["nfip"] or "unknown"
            warn(f"FEMA probe skipped: {type(error).__name__}: {error}. Last successful FEMA fetch in the published manifest: {last}. FEMA freshness is unverified.")
    except (SourceChanged, Blocked, Unavailable, httpx.HTTPStatusError, httpx.TransportError) as error:
        warn(f"probe could not prove the dataset is current: {type(error).__name__}: {error}")
        github_output(needed="true", card_only="false")
        return 0
    finally:
        fetcher.close()
    decision = probe_decision(manifest, card, head)
    card_only = decision["reasons"] == ["dataset card render changed"]
    print(json.dumps({**decision, "card_only": card_only, "head": head, "requests": fetcher.requests}, indent=1))
    github_output(needed="true" if decision["needed"] else "false", card_only="true" if card_only else "false")
    return 0

def cmd_check_pins(args):
    fetcher = Fetcher()
    try:
        report = {"bia_notices": tribes.latest(fetcher), "locus": locus.latest()}
    except (SourceChanged, Blocked, Unavailable) as error:
        warn(f"pin check could not finish: {type(error).__name__}: {error}", level="error")
        return 1
    finally:
        fetcher.close()
    problems = []
    latest = report["bia_notices"][:2]
    pinned = [tribes.NOTICE["document_number"], tribes.PREVIOUS["document_number"]]
    if [item["document_number"] for item in latest] != pinned:
        problems.append(f"Federal Register has newer BIA recognized-Tribes notices: latest two are {[item['document_number'] for item in latest]}, pinned are {pinned}; review the new notice and update local_laws/tribes.py pins before adopting it")
    if report["locus"].get("sha") != locus.REVISION:
        problems.append(f"LOCUS-v1 latest revision is {report['locus'].get('sha')}, pinned revision is {locus.REVISION}; review the new release before updating local_laws/locus.py")
    print(json.dumps(report, indent=1))
    for problem in problems:
        warn(problem, level="error")
    return 1 if problems else 0

def cmd_check_freshness(args):
    store = open_store(args)
    text = store.read_text(MANIFEST)
    manifest = json.loads(text) if text else {}
    source = (manifest.get("sources") or {}).get("nfip_communities") or {}
    head, unreadable = None, None
    fetcher = Fetcher()
    try:
        head = nfip_head(fetcher)
    except (Blocked, Unavailable, httpx.HTTPStatusError, httpx.TransportError) as error:
        unreadable = f"{type(error).__name__}: {error}"
    finally:
        fetcher.close()
    report = nfip.freshness(source, head)
    if unreadable:
        report["unreadable"] = unreadable
    print(json.dumps(report, indent=1))
    for problem in report["problems"]:
        warn(problem, level="error")
    if report["status"] == "unverified":
        warn(f"FEMA freshness is unverified; the oldest published source reading is {report['age_hours']:.1f} hours old, below the {report['max_unverified_hours']}-hour alert limit.")
    return 1 if report["problems"] else 0

def cmd_verify(args):
    from .verify import verify

    store = open_store(args)
    fetcher = None if args.offline else Fetcher()
    try:
        report = verify(store, fetcher=fetcher, stated_rows=None if args.offline else locus.stated_rows)
    except (SourceChanged, Blocked, Unavailable) as error:
        print(f"stopped: {type(error).__name__}: {error}", file=sys.stderr)
        return STOPPED
    finally:
        if fetcher:
            fetcher.close()
    print(json.dumps(report, indent=1))
    return 1 if report["problems"] else 0

def cmd_card(args):
    """Re-renders the card from the published manifest, for a card change that needs no rebuild."""
    if publishable(args) is None:
        return STOPPED
    try:
        store = open_store(args, write=True)
    except HfHubHTTPError as error:
        if trusted_publisher_error(error, args.repo):
            return 1
        raise
    text = store.read_text(MANIFEST)
    if text is None:
        print(f"no {MANIFEST}: run `python -m local_laws run` first", file=sys.stderr)
        return STOPPED
    card = render(json.loads(text))
    if store.read_text(CARD) == card:
        print("card unchanged")
        return 0
    with tempfile.TemporaryDirectory(prefix="local-laws-card-") as directory:
        path = Path(directory) / CARD
        path.write_text(card)
        try:
            store.commit({CARD: str(path)}, "Update dataset card")
        except HfHubHTTPError as error:
            if trusted_publisher_error(error, args.repo):
                return 1
            raise
        except Superseded as error:
            print(f"not written: {error}", file=sys.stderr)
            return 1
    print("card updated")
    return 0

def cmd_harvest_ny(args):
    """Reads New York's local-law category into a snapshot file that `run --ny-snapshot` builds from, so a build can be repeated without reading the API again."""
    fetcher = Fetcher()
    try:
        snapshot = nylaws.harvest(fetcher)
    except (SourceChanged, Blocked, Unavailable) as error:
        print(f"stopped, nothing written: {type(error).__name__}: {error}", file=sys.stderr)
        return STOPPED
    finally:
        fetcher.close()
    nylaws.save(snapshot, args.out)
    print(json.dumps({"out": args.out, "total": snapshot["total"], "years": snapshot["years"], "started_at": snapshot["started_at"], "finished_at": snapshot["finished_at"], "requests": fetcher.requests}, indent=1))
    return 0

def cmd_harvest_nfip(args):
    """Reads FEMA's Community Status Book and the OpenFEMA file it is checked against into a snapshot that `run --nfip-snapshot` builds from; checks that the two can be read and reconciled before writing it."""
    fetcher = Fetcher()
    try:
        snapshot = nfip.harvest(fetcher)
        table = nfip.rows(snapshot["csv"])
        reconciled = nfip.reconcile(table, snapshot["api"])
    except (SourceChanged, Blocked, Unavailable) as error:
        print(f"stopped, nothing written: {type(error).__name__}: {error}", file=sys.stderr)
        return STOPPED
    finally:
        fetcher.close()
    nfip.save(snapshot, args.out)
    print(json.dumps({"out": args.out, "rows": len(table), "api_rows": reconciled["rows"], "only_in_api": reconciled["only_in_api"], "retrieved_at": snapshot["retrieved_at"], "api_retrieved_at": snapshot["api_retrieved_at"], "requests": fetcher.requests}, indent=1))
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(prog="local_laws", description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text):
        sub = commands.add_parser(name, help=help_text, allow_abbrev=False)
        target = sub.add_mutually_exclusive_group()
        target.add_argument("--repo", default=REPO_ID, help=f"Hugging Face dataset repo (default {REPO_ID})")
        target.add_argument("--local", help="a local directory instead of the Hub, for tests and dry runs")
        sub.set_defaults(handler=handler)
        return sub

    add("probe", cmd_probe, "cheaply decide whether New York, FEMA or the dataset card changed")
    add("check-pins", cmd_check_pins, "fail if a manually pinned source has a newer release to review")
    add("check-freshness", cmd_check_freshness, "check published FEMA files against the live source; fail if changed or unreadable beyond the stored-reading age limit")
    run = add("run", cmd_run, "download the sources, check them, build the tables and commit them with the manifest and card")
    run.add_argument("--workdir", help="keep scratch files here (default: a temporary directory, deleted afterwards); LOCUS's download needs about 2 GB")
    run.add_argument("--ny-snapshot", help="build New York's table from a snapshot harvest-ny wrote, instead of reading the API again (about 1,500 requests)")
    run.add_argument("--nfip-snapshot", help="build the flood insurance communities' table from a snapshot harvest-nfip wrote, instead of reading fema.gov again")
    add("verify", cmd_verify, "check the published files against the manifest, each other, CG2200ORG02, LOCUS's card, the NY API's counts at the reading, the NY index's release, the BIA's notices and FEMA's report").add_argument(
        "--offline", action="store_true", help="skip the checks that download: CG2200ORG02, LOCUS's card, the NY index's release, the BIA's notices and FEMA's report")
    add("card", cmd_card, "re-render README.md from the published manifest")
    harvest = commands.add_parser("harvest-ny", help="read New York's local-law filings from the Department of State's API into a snapshot file", allow_abbrev=False)
    harvest.add_argument("--out", required=True, help="where to write the snapshot (gzipped JSON)")
    harvest.set_defaults(handler=cmd_harvest_ny)
    harvest = commands.add_parser("harvest-nfip", help="read FEMA's Community Status Book and the OpenFEMA file it is checked against into a snapshot file", allow_abbrev=False)
    harvest.add_argument("--out", required=True, help="where to write the snapshot (a zip)")
    harvest.set_defaults(handler=cmd_harvest_nfip)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from huggingface_hub.utils import disable_progress_bars

    disable_progress_bars()
    return args.handler(args)
