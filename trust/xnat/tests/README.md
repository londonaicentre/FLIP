# XNAT Tests

Tests for the FLIP XNAT deployment: the site-wide DICOM anonymization script
(`trust/xnat/xnat/config/anon_script.das`) and the weak-password guards that
protect the XNAT database credentials (FLIP-PT-056).

## What this covers

- **Static checks** (`test_anon_script_static.py`) — parse the `.das` file
  and assert that PHI tags FLIP must handle on every study (Patient ID,
  Patient Name, SOP/Study/Series UIDs, etc.) all have an explicit rule.
  Catches future regressions where someone deletes a rule.
- **Synthetic DICOM PHI checks** (`test_anon_script_phi.py`) — build
  in-memory DICOM datasets populated with PHI in every tag the script
  references, run a Python interpreter for the `.das` rule subset used
  by FLIP, and assert each PHI tag is removed, replaced, or hashed.
- **Negative parser checks** (`test_anon_engine_negative.py`) — feed the
  parser known-bad `.das` snippets and assert each one raises
  `UnsupportedRuleError`. Pins the "fail loud" contract so a future grammar
  change cannot silently start accepting unknown directives.
- **Password-guard parity** (`test_password_guards.py`) — four layers decide
  whether an XNAT database password is real or a known-weak placeholder: the
  credential minter (`flip-api/.../generate_xnat_credentials.py`), the deploy
  guard (`trust/xnat/Makefile`), and the two entrypoint guards
  (`wait-for-postgres.sh`, `guard-xnat-db-passwords.sh`). They cannot share one
  list at runtime — the XNAT images carry no Python, and each image's build
  context is its own leaf directory — so these tests parse all four and fail if
  any one drifts. Without them a value one layer calls strong and the next calls
  weak strands the operator between two tools that disagree.
- **Guard behaviour** (`test_guard_scripts.py`) — parity pins *what* each layer
  calls weak, by reading its source; it cannot see whether the guard still runs.
  An errant `exit 0`, a `case` block that drifts below the `exec`, or a reverted
  `exec "$@"` all keep the literals intact and pass every static assertion. So
  these tests execute the two entrypoint guards as real subprocesses across the
  reject / accept / retry matrix, under each POSIX shell on the runner (the two
  images ship different ones: dash for xnat-web, busybox ash for xnat-db). The
  reject cases also assert the `psql` stub was *never* called, which is what pins
  the guard ahead of any database contact.
- **Startup reliability** (`test_startup.py`) — executes the plugin cache/download
  guard and authenticated plugin-readiness wait with stubbed AWS/curl commands,
  checks aggregate Make targets propagate failures, and pins root-relative paths
  passed to the end-to-end smoke test.

The tests do not stand up XNAT, DicomEdit, or Postgres. The anonymization tests
validate the FLIP-authored ruleset against synthetic studies; the guard-behaviour
tests stub the few external commands the scripts reach for (`docker-entrypoint.sh`,
`psql`) onto `PATH`, the same mock-bin idiom as
`deploy/providers/AWS/scripts/tests/test_add_fl_kits.sh`. So a regression is caught
in CI without a heavyweight integration environment or an image build.

## Running

```bash
make unit_test    # ruff + mypy + pytest
make test         # alias of unit_test
```

Run from `trust/xnat/tests/` or via `make -C trust/xnat/tests unit_test`.

## When to update

If you add a tag to `anon_script.das`, also add it to the
`PHI_TAGS_REQUIRED` allowlist in `test_anon_script_static.py` (if it is
a tag FLIP guarantees handling for) and to the synthetic study fixture
in `conftest.py`.

If you change what counts as a weak XNAT password, change it in **all four**
places and update `CANONICAL_WEAK_VALUES` in `test_password_guards.py` — the
tests fail until they agree. `test_guard_scripts.py` imports that same set, so a
new value is exercised against both entrypoints automatically. Note these read
files from outside this directory, so `.github/workflows/test_trust_xnat.yml`
lists each of them in its paths filter; a new input needs adding there too, or
the guard change lands with its test unrun.
