"""Where the dataset lives: a Hugging Face dataset repo, or a local directory for tests and dry runs.

A build writes both tables, manifest.json and the card (README.md, rendered from the manifest) in one commit, so the four cannot disagree on the Hub.
Every Hub commit names its parent (parent_commit). If anything else committed since this store read the repo, the Hub refuses the commit and the store raises Superseded instead of overwriting the other writer's work.
"""

import hashlib
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import CommitOperationAdd, HfApi
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError, RemoteEntryNotFoundError

MANIFEST = "manifest.json"
CARD = "README.md"
# The Hub adds this to every new repo.
HUB_FILES = (".gitattributes",)
COMMIT_ATTEMPTS = 4
RETRYABLE = (408, 429, 500, 502, 503, 504)
# Failures with no HTTP status that are still a busy Hub or network, so they are retried as well: a dropped connection or timeout, and a README check answered with a page that is not JSON. huggingface_hub 1.32.0 parses the validate-yaml body before it checks the status, so a busy Hub raises JSONDecodeError there, before anything is uploaded; the Fed scheduled run of September 27, 2026 stopped on one.
TRANSIENT = (httpx.TransportError, json.JSONDecodeError)
# Measured September 23, 2026 on a scratch dataset for the CRS pipeline: a commit whose parent_commit is no longer the branch head answers 412 Precondition Failed.
CONFLICT = 412
log = logging.getLogger("local_laws")


class Superseded(RuntimeError):
    """Another writer committed to the repo since this store last saw it."""


def write_parquet(rows, path, schema, sort_key):
    """Rows sorted by sort_key, zstd, content-defined chunking so a rebuild that changes a few rows re-uploads only the chunks they are in."""
    table = pa.Table.from_pylist(sorted(rows, key=sort_key), schema=schema)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", compression_level=9, use_content_defined_chunking=True)
    return {"rows": table.num_rows, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)
        self.commits = []

    def read_text(self, repo_path):
        path = self.root / repo_path
        return path.read_text() if path.exists() else None

    def read_bytes(self, repo_path):
        path = self.root / repo_path
        return path.read_bytes() if path.exists() else None

    def read_table(self, repo_path):
        path = self.root / repo_path
        return pq.read_table(path) if path.exists() else None

    def list_files(self):
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())

    def file_sha256s(self, repo_paths):
        return {repo_path: sha256_file(self.root / repo_path) for repo_path in repo_paths if (self.root / repo_path).exists()}

    def commit(self, files, message):
        """files maps repo paths to local files."""
        for repo_path, local in sorted(files.items()):
            target = self.root / repo_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target)
        self.commits.append({"message": message, "files": sorted(files)})
        return str(len(self.commits))


class HubStore:
    """token=False reads anonymously (the dataset is public). create=True makes the public repo first if it does not exist. A commit by another writer that changed only paths in tolerate, which this store never writes, does not supersede it: its commit goes on top."""

    def __init__(self, repo_id, token=None, api=None, create=False, tolerate=()):
        self.repo_id = repo_id
        self.api = api or HfApi(token=token)
        self.tolerate = frozenset(tolerate)
        if create:
            self.api.create_repo(repo_id, repo_type="dataset", private=False, exist_ok=True)
        self.revision = self.api.dataset_info(repo_id).sha
        self.superseded = None

    def _download(self, repo_path, directory):
        try:
            return Path(self.api.hf_hub_download(self.repo_id, repo_path, repo_type="dataset", revision=self.revision, local_dir=directory))
        except (EntryNotFoundError, RemoteEntryNotFoundError):
            return None

    def read_text(self, repo_path):
        with tempfile.TemporaryDirectory(prefix="local-laws-") as directory:
            local = self._download(repo_path, directory)
            return local.read_text() if local else None

    def read_bytes(self, repo_path):
        with tempfile.TemporaryDirectory(prefix="local-laws-") as directory:
            local = self._download(repo_path, directory)
            return local.read_bytes() if local else None

    def read_table(self, repo_path):
        with tempfile.TemporaryDirectory(prefix="local-laws-") as directory:
            local = self._download(repo_path, directory)
            return pq.read_table(local) if local else None

    def list_files(self):
        return sorted(self.api.list_repo_files(self.repo_id, repo_type="dataset", revision=self.revision))

    def file_sha256s(self, repo_paths):
        """SHA-256 of each LFS file, as the Hub reports it, so checking a file never downloads it."""
        out = {}
        for info in self.api.get_paths_info(self.repo_id, list(repo_paths), repo_type="dataset", revision=self.revision):
            lfs = getattr(info, "lfs", None)
            if lfs is not None:
                out[info.path] = lfs.sha256
        return out

    def commit(self, files, message):
        """One atomic commit of files (repo path -> local file) on top of the last commit this store saw. Retries rate limits, server errors and the TRANSIENT failures; a retry that finds its own manifest already at the head counts as landed."""
        if self.superseded:
            raise self.superseded
        operations = [CommitOperationAdd(path_in_repo=repo_path, path_or_fileobj=str(local)) for repo_path, local in sorted(files.items())]
        for attempt in range(COMMIT_ATTEMPTS):
            try:
                oid = self.api.create_commit(self.repo_id, operations=operations, commit_message=message, repo_type="dataset", parent_commit=self.revision).oid
                break
            except (HfHubHTTPError, *TRANSIENT) as error:
                http = isinstance(error, HfHubHTTPError)
                status = getattr(error.response, "status_code", None) if http else None
                if status == CONFLICT:
                    oid = self._landed(files.get(MANIFEST))
                    if oid:
                        log.warning("commit %r had already landed as %s", message[:60], oid[:12])
                        break
                    head = self._tolerated_head(files)
                    if head and attempt < COMMIT_ATTEMPTS - 1:
                        log.warning("%s changed only %s since %s; committing on top of %s", self.repo_id, sorted(self.tolerate), self.revision[:12], head[:12])
                        self.revision = head
                        continue
                    self.superseded = Superseded(f"{self.repo_id} has a commit this run did not write (its last commit was {self.revision[:12]})")
                    raise self.superseded from None
                if attempt == COMMIT_ATTEMPTS - 1 or (http and status not in RETRYABLE):
                    raise
                log.warning("commit attempt %d failed with %s; retrying", attempt + 1, f"HTTP {status}" if http else type(error).__name__)
                time.sleep(60 * (attempt + 1))
        self.revision = oid
        return oid

    def _tree(self, revision):
        return {entry.path: getattr(entry, "blob_id", None) for entry in self.api.list_repo_tree(self.repo_id, recursive=True, repo_type="dataset", revision=revision) if hasattr(entry, "blob_id")}

    def _tolerated_head(self, files):
        """The head commit if every change since this store's revision is to a tolerated path that this commit does not write, else None."""
        if not self.tolerate or self.tolerate & set(files):
            return None
        head = self.api.dataset_info(self.repo_id).sha
        before, after = self._tree(self.revision), self._tree(head)
        changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
        return head if changed and changed <= self.tolerate else None

    def _landed(self, manifest):
        """The head commit if it already holds exactly this manifest, else None."""
        if manifest is None:
            return None
        head = self.api.dataset_info(self.repo_id).sha
        data = Path(manifest).read_bytes()
        for info in self.api.get_paths_info(self.repo_id, [MANIFEST], repo_type="dataset", revision=head):
            lfs = getattr(info, "lfs", None)
            if (lfs.sha256 == hashlib.sha256(data).hexdigest()) if lfs else getattr(info, "blob_id", None) == git_blob_sha1(data):
                return head
        return None
