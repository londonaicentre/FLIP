# FL round-timing metrics

Tooling to extract and compare **per-round FL timings** (how long each global
round and its aggregation took) from either place a FLIP training run can
happen: a platform run, read from the Central Hub's logs, or a local FLARE
simulator.

These are wall-clock platform timings, not model performance. They are separate
from FLIP's *model metrics* (the `FLMetrics` training curves served by
`/model/{id}/metrics` and plotted in the UI).

| Tool | Source | Needs |
|------|--------|-------|
| `extract_platform_round_timings.sh` | Central Hub CloudWatch logs (`/ecs/flip-api`, `/ecs/fl-api-net-*`, `/ecs/fl-server-net-*`) for one model, FLARE or Flower | read-only AWS credentials |
| `extract_simulator_round_timings.sh` | A local FLARE simulator workspace (FLIP `ScatterAndGather` controller events in the server log) | nothing beyond the workspace |
| `plot_round_timings.py` | A `rounds.tsv` from either extractor | `uv run` (matplotlib) |

The Ark+ fine-tuning tutorial
([`fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/`](../../fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/))
wraps the simulator side as `make round-metrics` / `make reproduce-overhead`.
The full cross-site protocol is in
[Reproducing the FLIP platform-overhead experiment](../../docs/source/paper/reproducing-platform-overhead.rst).

## The `rounds.tsv` schema is the contract

Both extractors emit the same `rounds.tsv`. That is what lets a simulator
baseline be compared directly with a platform run
(`extract_simulator_round_timings.sh --compare`), and it is the file
`plot_round_timings.py` reads. **If you change these columns, change both
extractors, the plotter, and the `--compare` header validation together** —
they live in one directory precisely so that drift is a one-directory diff:

```text
round  started_utc  finished_utc  duration_s  agg_started_utc  agg_finished_utc  agg_duration_s  accepted  rejected  start_ms  end_ms  agg_start_ms  agg_end_ms
```

- `round` — global round number as the controller logs it, counting from 0
  (round 0 carries the one-off initialisation cost, so the summaries report
  steady-state stats over rounds >= 1 separately).
- `*_utc` — ISO-8601 timestamps; `-` when the event was not observed.
- `duration_s` / `agg_duration_s` — derived spans in seconds.
- `accepted` / `rejected` — client contributions the aggregator accepted/rejected.
- `*_ms` — raw epoch-millisecond counterparts of the timestamp columns.

Each extractor also writes a human-readable `summary.md` (with explicit
`NOT AVAILABLE` / `NOT APPLICABLE` / `PARTIAL DATA` markers rather than silent
gaps) and a timing boxplot via the plotter.

## Tests

```bash
bash scripts/fl_round_metrics/tests/run_extractor_tests.sh
```

Needs only `bash` + coreutils — no AWS, no GPU, no simulator run. CI runs it on
`ubuntu-latest` (`.github/workflows/fl_round_metrics_tests.yml`), where the default
`awk` is **mawk**; that is deliberate (see *Why a golden diff rather than unit tests*).

`tests/fixtures/` holds synthetic simulator logs and the golden `rounds.tsv` each
should produce. `--log` is the injection point, so a test is "run the extractor
over a known log, diff the table". Coverage: per-round contribution tags vs an
untagged line, a round that never finishes, an aborted run where nothing
finishes, ANSI-coloured log lines, and `--compare` validation plus its overhead
table.

**Why a golden diff rather than unit tests.** The parsing layer is a pipeline of
grep/sed/awk stages that fail *silently* — they emit a plausible-looking
`rounds.tsv` instead of an error, and the numbers land in a paper. Two such bugs
have already shipped here: a mawk-vs-gawk `OFMT` difference that reformatted
epoch milliseconds, and a `grep` filename prefix that passed raw log lines
through as round data. Neither is visible by reading the code; both die instantly
against a known-good table.

**Regenerating a golden.** Do this only when a schema or parsing change is
*intended*. Before committing, read the golden's diff line by line: reviewing
that diff is the whole point of the test.

```bash
TZ=UTC scripts/fl_round_metrics/extract_simulator_round_timings.sh \
    --log scripts/fl_round_metrics/tests/fixtures/<name>.log \
    --run-name <name> -o /tmp/regen
cp /tmp/regen/<name>/rounds.tsv scripts/fl_round_metrics/tests/fixtures/<name>.rounds.tsv
```

`TZ=UTC` is required: simulator logs carry local wall-clock time with no zone, and
`rounds.tsv` records absolute epoch milliseconds, so the goldens only reproduce
under a pinned timezone.
