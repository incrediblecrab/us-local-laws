"""HubStore against a fake Hub that behaves as the real one does where it matters: a commit whose parent is not the head is refused with 412, parquet and zip files are LFS (a SHA-256), as the dataset's .gitattributes makes them, and text files are git blobs (a SHA-1)."""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pyarrow as pa
import pytest
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError

from conftest import NY_SAMPLE, FakeFetcher
from local_laws import store as store_module
from local_laws.store import MANIFEST, HubStore, Superseded, git_blob_sha1, write_parquet
from local_laws.verify import verify


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://huggingface.co/api/datasets/x/y/commit/main"))
    return HfHubHTTPError(f"{status} from the fake Hub", response=response)


class FakeApi:
    """One branch. failures holds (error, lands) pairs that create_commit raises in turn; lands=True applies the commit first, as when a response is lost after the Hub wrote it."""

    def __init__(self, files=None, lfs=(".parquet", ".zip")):
        self.files = dict(files or {})
        self.lfs = lfs
        self.commits = 0
        self.failures = []
        self.calls = []
        self.history = {self.head: dict(self.files)}

    @property
    def head(self):
        return f"commit-{self.commits}"

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        self.calls.append("create_repo")
        assert (repo_type, private, exist_ok) == ("dataset", False, True)

    def dataset_info(self, repo_id, revision=None):
        self.calls.append("dataset_info")
        return SimpleNamespace(sha=self.head)

    def create_commit(self, repo_id, operations, commit_message, repo_type, parent_commit):
        self.calls.append("create_commit")
        if parent_commit != self.head:
            raise http_error(412)
        if self.failures:
            error, lands = self.failures.pop(0)
            if lands:
                self.apply(operations)
            raise error
        return SimpleNamespace(oid=self.apply(operations))

    def apply(self, operations):
        for operation in operations:
            self.files[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
        self.commits += 1
        self.history[self.head] = dict(self.files)
        return self.head

    def other_writer(self, path=MANIFEST, data=b'{"other": true}\n'):
        self.files[path] = data
        self.commits += 1
        self.history[self.head] = dict(self.files)

    def list_repo_tree(self, repo_id, recursive, repo_type, revision):
        self.calls.append("list_repo_tree")
        return [SimpleNamespace(path=path, blob_id=git_blob_sha1(data)) for path, data in sorted(self.history[revision].items())]

    def get_paths_info(self, repo_id, paths, repo_type, revision):
        self.calls.append("get_paths_info")
        out = []
        for path in paths:
            if path in self.files:
                data = self.files[path]
                lfs = SimpleNamespace(sha256=hashlib.sha256(data).hexdigest()) if path.endswith(self.lfs) else None
                out.append(SimpleNamespace(path=path, lfs=lfs, blob_id=git_blob_sha1(data)))
        return out

    def hf_hub_download(self, repo_id, path, repo_type, revision, local_dir):
        if path not in self.files:
            raise EntryNotFoundError(path)
        local = Path(local_dir) / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[path])
        return str(local)

    def list_repo_files(self, repo_id, repo_type, revision):
        return sorted(self.files) + [".gitattributes"]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(store_module.time, "sleep", slept.append)
    return slept


def local_files(tmp_path, manifest=b'{"build": 1}\n'):
    (tmp_path / MANIFEST).write_bytes(manifest)
    (tmp_path / "data.parquet").write_bytes(b"PAR1 not really")
    return {MANIFEST: str(tmp_path / MANIFEST), "data/x.parquet": str(tmp_path / "data.parquet")}


def test_a_commit_lands_on_the_head_it_read(tmp_path):
    api = FakeApi()
    hub = HubStore("x/y", api=api, create=True)
    assert api.calls[:2] == ["create_repo", "dataset_info"]
    assert hub.commit(local_files(tmp_path), "build") == "commit-1" == hub.revision
    assert api.files[MANIFEST] == b'{"build": 1}\n'
    assert hub.commit(local_files(tmp_path, b'{"build": 2}\n'), "build 2") == "commit-2", "a store's own commits move its parent along"


def test_a_commit_after_another_writer_is_superseded_and_the_store_stops(tmp_path):
    api = FakeApi()
    hub = HubStore("x/y", api=api)
    api.other_writer()
    with pytest.raises(Superseded):
        hub.commit(local_files(tmp_path), "build")
    assert api.files[MANIFEST] == b'{"other": true}\n', "the other writer's work stands"
    calls = len(api.calls)
    with pytest.raises(Superseded):
        hub.commit(local_files(tmp_path), "again")
    assert len(api.calls) == calls, "a superseded store makes no further Hub calls"


def test_a_lost_response_whose_commit_landed_is_adopted(tmp_path, no_sleep):
    api = FakeApi()
    api.failures = [(http_error(502), True)]
    hub = HubStore("x/y", api=api)
    assert hub.commit(local_files(tmp_path), "build") == "commit-1"
    assert api.commits == 1, "the retry did not commit twice"
    assert no_sleep == [60]


@pytest.mark.parametrize("lfs", [(".parquet",), (".parquet", ".json")], ids=["manifest-as-git-blob", "manifest-as-lfs"])
def test_a_retry_knows_its_own_manifest_by_either_hash_the_hub_reports(tmp_path, lfs):
    api = FakeApi(lfs=lfs)
    api.failures = [(http_error(503), True)]
    hub = HubStore("x/y", api=api)
    files = local_files(tmp_path)
    assert hub._landed(files[MANIFEST]) is None, "nothing has landed yet"
    assert hub.commit(files, "build") == "commit-1" and api.commits == 1
    api.files[MANIFEST] = b'{"build": 0}\n'
    assert hub._landed(files[MANIFEST]) is None, "another manifest at the head is not this one"


def test_retries_are_bounded_and_client_errors_are_not_retried(tmp_path, no_sleep):
    api = FakeApi()
    api.failures = [(http_error(503), False)] * 4
    hub = HubStore("x/y", api=api)
    with pytest.raises(HfHubHTTPError):
        hub.commit(local_files(tmp_path), "build")
    assert (api.calls.count("create_commit"), no_sleep) == (4, [60, 120, 180])
    api.failures = [(http_error(400), False)]
    with pytest.raises(HfHubHTTPError):
        hub.commit(local_files(tmp_path), "build")
    assert api.calls.count("create_commit") == 5 and api.commits == 0


def test_write_parquet_is_deterministic_whatever_the_row_order(tmp_path):
    schema = pa.schema([("id", pa.string()), ("n", pa.int64())])
    rows = [{"id": str(i), "n": i * i} for i in range(1000)]
    first = write_parquet(rows, tmp_path / "a.parquet", schema, lambda row: row["id"])
    second = write_parquet(list(reversed(rows)), tmp_path / "b.parquet", schema, lambda row: row["id"])
    assert first == second and first["rows"] == 1000


def test_a_busy_readme_check_and_a_dropped_connection_are_retried(tmp_path, no_sleep):
    """huggingface_hub parses the validate-yaml body before it checks the status, so a busy Hub raises JSONDecodeError before anything is uploaded (the Fed scheduled run of September 27, 2026 stopped on one); a timeout can come after the commit landed."""
    api = FakeApi()
    api.failures = [(json.JSONDecodeError("Expecting value", "", 0), False), (httpx.ReadTimeout("planted: the response never arrived"), True)]
    hub = HubStore("x/y", api=api)
    assert hub.commit(local_files(tmp_path), "build") == "commit-1"
    assert api.commits == 1 and no_sleep == [60, 120], "retried twice, and the landed commit was adopted rather than made again"


def test_invalid_card_metadata_is_raised_at_once(tmp_path):
    api = FakeApi()
    api.failures = [(ValueError("Invalid metadata in README.md."), False)]
    hub = HubStore("x/y", api=api)
    with pytest.raises(ValueError, match="Invalid metadata"):
        hub.commit(local_files(tmp_path), "build")
    assert api.calls.count("create_commit") == 1, "JSONDecodeError is a ValueError, but a card the Hub rejects is not retried"


def test_git_blob_sha1_is_git_s():
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_a_published_build_verifies_through_the_hub_store(published, tmp_path):
    local, manifest = published
    api = FakeApi({path: (local.root / path).read_bytes() for path in local.list_files()})
    hub = HubStore("x/y", token=False, api=api)
    report = verify(hub, fetcher=FakeFetcher(), stated_rows=lambda: manifest["sources"]["locus"]["rows"])
    assert report["problems"] == [] and report["rows"] == {"governments": 50, "locus_crosswalk": 25, "ny_local_laws": NY_SAMPLE["total"], "ny_local_law_index": 14, "federally_recognized_tribes": 19, "nfip_communities": 25}
    api.files["data/governments.parquet"] = api.files["data/governments.parquet"][:-1] + b"!"
    shutil.rmtree(tmp_path / "work", ignore_errors=True)
    problems = verify(HubStore("x/y", api=api))["problems"]
    assert any(problem.startswith("data/governments.parquet has SHA-256") for problem in problems), problems


def test_a_commit_goes_on_top_of_another_writer_that_changed_only_a_tolerated_path(tmp_path):
    api = FakeApi({MANIFEST: b'{"build": 0}\n'})
    hub = HubStore("x/y", api=api, tolerate=("sources/harvest.zip",))
    api.other_writer("sources/harvest.zip", b"newer reading")
    assert hub.commit(local_files(tmp_path), "build") == "commit-2" == hub.revision
    assert (api.files[MANIFEST], api.files["sources/harvest.zip"]) == (b'{"build": 1}\n', b"newer reading"), "both writers' work stands"


@pytest.mark.parametrize("others", [
    [("sources/harvest.zip", b"newer reading"), (MANIFEST, b'{"other": true}\n')],
    [(MANIFEST, b'{"other": true}\n')],
])
def test_another_writer_that_changed_more_than_a_tolerated_path_still_supersedes(tmp_path, others):
    api = FakeApi({MANIFEST: b'{"build": 0}\n'})
    hub = HubStore("x/y", api=api, tolerate=("sources/harvest.zip",))
    for path, data in others:
        api.other_writer(path, data)
    with pytest.raises(Superseded):
        hub.commit(local_files(tmp_path), "build")
    assert api.files[MANIFEST] == b'{"other": true}\n'


def test_a_commit_that_writes_a_tolerated_path_is_superseded_by_any_other_writer(tmp_path):
    api = FakeApi()
    hub = HubStore("x/y", api=api, tolerate=("sources/harvest.zip",))
    api.other_writer("sources/harvest.zip", b"newer reading")
    (tmp_path / "h.zip").write_bytes(b"mine")
    with pytest.raises(Superseded):
        hub.commit({"sources/harvest.zip": str(tmp_path / "h.zip")}, "harvest")
    assert api.files["sources/harvest.zip"] == b"newer reading"
