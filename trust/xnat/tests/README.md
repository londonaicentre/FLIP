# XNAT Anonymization Tests

Tests for `trust/xnat/xnat/config/anon_script.das`, the FLIP site-wide
DICOM anonymization script.

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
