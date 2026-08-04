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

The tests do not stand up XNAT or DicomEdit. They validate the FLIP
authored ruleset against synthetic studies so a regression in the
script is caught in CI without a heavyweight integration environment.

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
tests fail until they agree. Note these read files from outside this directory,
so `.github/workflows/test_trust_xnat_anon.yml` lists each of them in its paths
filter; a new input needs adding there too, or the guard change lands with its
test unrun.
