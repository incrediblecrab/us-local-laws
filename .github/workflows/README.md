# workflows

**Objective:** keep the Hugging Face dataset current with no personal device and no stored Hugging Face token. The workflow runs at 00:00 and 12:00 UTC.

**Inputs:** GitHub's OIDC token, which Hugging Face exchanges for a one-hour dataset-scoped write token when the Hub dataset has this repository, branch and `pipeline.yml` registered as a Trusted Publisher. No `HF_TOKEN` secret is used.

**Files:**

- [`pipeline.yml`](pipeline.yml): `probe` compares New York filing-year counts, FEMA file hashes and the published card with cheap reads. When only the card render changed, `card` updates it without a full build; otherwise `run` harvests New York and FEMA, builds from pinned sources, writes only if changed, verifies the published commit, then checks pinned sources for newer releases a person must review. If fema.gov refuses a GitHub-hosted runner, as it returned HTTP 403 on September 27, 2026, the run logs a warning with the error and builds the FEMA table from the FEMA files the last build stored in the dataset, once they match the manifest, so New York updates still publish; with no stored files it writes no commit. `inactivity` fails after 50 days without a commit; it makes no keepalive commit because GitHub treats that as circumventing the 60-day schedule disablement policy. `.github/` has no README, which GitHub would show instead of the repository's.
- [`test.yml`](test.yml): runs the test suite on every push to `main` and every pull request, with read-only contents and no OIDC token, so it cannot publish.
