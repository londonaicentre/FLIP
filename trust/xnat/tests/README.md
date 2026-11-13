# XNAT Tests

Tests for the FLIP XNAT deployment: the site-wide DICOM anonymization script
(`trust/xnat/xnat/config/anon_script.das`) and the weak-password guards that
protect the XNAT database credentials (FLIP-PT-056).

## What this covers

The anonymisation and password-guard suites below. The other `test_*.py` modules here guard the XNAT
deploy tooling (plugins, PACS and export-mask configuration, the Makefile, startup); each says what it
pins in its docstring.

- **Static checks** (`test_anon_script_static.py`) — parse the `.das` file
  and assert that PHI tags FLIP must handle on every study (Patient ID,
  Patient Name, SOP/Study/Series UIDs, etc.) all have an explicit rule.
  Catches future regressions where someone deletes a rule.
- **Synthetic DICOM PHI checks** (`test_anon_script_phi.py`) — build
  in-memory DICOM datasets populated with PHI in every tag the script
  references, run a Python interpreter for the `.das` rule subset used
  by FLIP, and assert each PHI tag is removed, replaced, or hashed.
- **Negative parser checks** (`test_anon_engine_negative.py`) — feed
  `anon_engine.py`'s parser known-bad `.das` snippets and assert each one raises
  `UnsupportedRuleError`. Pins the "fail loud" contract so a future grammar
  change cannot silently start accepting unknown directives.
- **Password-guard parity** (`test_password_guards.py`) — four layers decide
  whether an XNAT database password is real or a known-weak placeholder: the
  credential minter (`flip-api/.../generate_xnat_credentials.py`), the deploy
  guard (`trust/xnat/Makefile`), and the two entrypoint guards
  (`wait-for-postgres.sh`, `guard-xnat-db-passwords.sh`). They cannot share one
  list at runtime — the XNAT images carry no Python, and each image's build
  context is its own leaf directory — so these tests parse all four and fail if
  any one drifts. Otherwise one layer could accept a password that the next
  rejects, leaving the operator stuck between two tools that disagree.
- **Guard behaviour** (`test_guard_scripts.py`) — parity pins *what* each layer
  calls weak, by reading its source; it cannot see whether the guard still runs.
  An errant `exit 0`, a `case` block that drifts below the `exec`, or a reverted
  `exec "$@"` all keep the literals intact and pass every static assertion. So
  these tests execute the two entrypoint guards as real subprocesses across the
  reject / accept / retry matrix, under each POSIX shell on the runner (the two
  images ship different ones: dash for xnat-web, busybox ash for xnat-db). The
  reject cases also assert the `psql` stub was *never* called, which is what pins
  the guard ahead of any database contact.
- **Third-party artifacts** (`test_artifacts.py`) — pins `artifacts.manifest` (well-formed
  lines, the WAR at the `.env` XNAT version, one jar per required plugin family, the same plugin
  URLs as the Helm chart) and executes `xnat_artifacts.sh` `check`/`fetch` against fixture
  manifests with a stubbed `curl` and an `aws` stub that must never run.
- **dcm2niix registration and trigger repair** (`test_dcm2niix_registration.py`, FLIP#1451) —
  runs `configure-dcm2niix.sh` for both Container Service backends against `fake_xnat.py`, a
  miniature XNAT behind a stubbed `curl`, and checks what XNAT ends up holding.
- **Startup reliability** (`test_startup.py`) — executes the dev plugin-cache check ordering
  and authenticated plugin-readiness wait with stubbed curl commands and fixture jars (`plugin_fixtures.py`),
  checks aggregate Make targets propagate failures, and pins root-relative paths
  passed to the end-to-end smoke test.
- **Anonymous XNAT paths** (`test_anonymous_endpoints.py`) — fails on an `XNAT_VERSION` bump until
  the new version's anonymous paths have been checked (the assert message says how).

The tests do not stand up XNAT, DicomEdit, or Postgres. The anonymization tests
validate the FLIP-authored ruleset against synthetic studies. The script tests put
stubs for the external commands they call (`docker-entrypoint.sh`, `psql`, `curl`,
`aws`) onto `PATH`, the same mock-bin idiom as
`deploy/providers/AWS/scripts/tests/test_add_fl_kits.sh`. So a regression is caught
in CI without a heavyweight integration environment or an image build.

## Running

From the repo root:

```bash
make -C trust/xnat/tests unit_test    # ruff + mypy + pytest
```

Or run `make unit_test` from `trust/xnat/tests/`; `make test` does the same.

## When to update

### The anonymization script

`anon_script.das` handles every row of DICOM PS3.15 Table E.1-1, copied into
`ps315_table_e1_1.csv`. `test_every_table_e1_1_attribute_is_handled` fails when
a row is not removed at any depth, hashed, or replaced.

When a new edition of the standard adds rows:

1. Refresh the copy with `extract_ps315_table.py` (its docstring has the commands).
2. Add a rule per new row. Write removals as `- */(gggg,eeee)`: a bare tag matches
   the top level only, and the static tests fail on one. A tag the script replaces
   at the top level needs `- +/(gggg,eeee)` for its nested copies.
3. If a test should name the tag explicitly, add it to `PHI_TAGS_REQUIRED` in
   `test_anon_script_static.py` and to the synthetic study fixture in `conftest.py`.

### The DicomEdit stand-in (`anon_engine.py`)

`anon_engine.py` is a stand-in for DicomEdit, so it only proves what it
models. Its `*/`, `+/`, `X`/`@` tag wildcards, `removeAllPrivateTags`,
`hashUID` on an absent tag, and `\\` semantics were checked against DicomEdit
6.9.0 from the `xnat-web` image (FLIP#1513). Both engines gave the same output on
the fixture and on real CT, MR, enhanced MR, RT structure set, secondary capture,
ultrasound and X-ray samples.

- **New construct in the script:** check it against DicomEdit the same way before
  relying on it.
- **XNAT version bump:** also run the script through the new WAR's DicomEdit. For
  XNAT 1.10.2.1 (FLIP#1648), DicomEdit 6.10.0 gave output identical to 6.9.0's on
  the fixture, with and without a private block, Digital Signatures Sequence and
  padding after Pixel Data.

DicomEdit rejects some forms outright (a space inside the tag brackets, a lowercase
`x`, `removeallprivatetags`), and then XNAT cannot parse the script at all.

### The weak-password list

If you change what counts as a weak XNAT password, change it in **all four**
places and update `CANONICAL_WEAK_VALUES` in `test_password_guards.py` — the
tests fail until they agree. `test_guard_scripts.py` imports that same set, so a
new value is exercised against both entrypoints automatically.

### Inputs outside this directory

Many of these tests read files from outside `trust/xnat/tests/`, so
`.github/workflows/test_trust_xnat.yml` lists each of them in its paths filter.
A new input needs adding there too, or a change to it lands with its test unrun.
