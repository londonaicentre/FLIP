# FL round-timing metrics

Tooling for extracting and comparing **per-round FL timings** — how long each
global round and aggregation took — from the two places a FLIP training run can
happen. Not to be confused with FLIP's *model metrics* (the `FLMetrics` training
curves served by `/model/{id}/metrics` and plotted in the UI): everything here
measures wall-clock platform behaviour, not model performance.

| Tool | Source | Needs |
|------|--------|-------|
| `extract_platform_round_timings.sh` | Central Hub CloudWatch logs (`/ecs/flip-api`, `/ecs/fl-api-net-*`, `/ecs/fl-server-net-*`) for one model, NVFLARE or Flower | read-only AWS credentials |
| `extract_simulator_round_timings.sh` | A local NVFLARE simulator workspace (FLIP `ScatterAndGather` controller events in the server log) | nothing beyond the workspace |
| `plot_round_timings.py` | A `rounds.tsv` from either extractor | `uv run` (matplotlib) |

The arkplus fine-tuning tutorial wraps the simulator side as
`make round-metrics` / `make reproduce-overhead`
(`fl-tutorials/nvflare/image_classification/arkplus_fine_tuning/`); the full
cross-site protocol is documented in
`docs/source/user-guides/reproducing-platform-overhead.rst`.

## The `rounds.tsv` schema is the contract

Both extractors emit an identical `rounds.tsv`, which is what makes a simulator
baseline directly comparable against a platform run (`--compare`), and what
`plot_round_timings.py` consumes. **If you change these columns, change both
extractors, the plotter, and the `--compare` header validation together** —
they live in one directory precisely so that drift is a one-directory diff:

```
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
`awk` is **mawk**; that is deliberate, see below.

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

**Regenerating a golden** (only when a schema or parsing change is *intended* —
read the diff line by line first, it is the whole point of the test):

```bash
TZ=UTC scripts/fl_round_metrics/extract_simulator_round_timings.sh \
    --log scripts/fl_round_metrics/tests/fixtures/<name>.log \
    --run-name <name> -o /tmp/regen
cp /tmp/regen/<name>/rounds.tsv scripts/fl_round_metrics/tests/fixtures/<name>.rounds.tsv
```

`TZ=UTC` is required: simulator logs carry local wall-clock time with no zone, and
`rounds.tsv` records absolute epoch milliseconds, so the goldens only reproduce
under a pinned timezone.
