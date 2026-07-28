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

- `round` — 1-based global round number.
- `*_utc` — ISO-8601 timestamps; `-` when the event was not observed.
- `duration_s` / `agg_duration_s` — derived spans in seconds.
- `accepted` / `rejected` — client contributions the aggregator accepted/rejected.
- `*_ms` — raw epoch-millisecond counterparts of the timestamp columns.

Each extractor also writes a human-readable `summary.md` (with explicit
`NOT AVAILABLE` / `NOT APPLICABLE` / `PARTIAL DATA` markers rather than silent
gaps) and a timing boxplot via the plotter.

## Tests

**None yet — nothing to run today.** `tests/` is the reserved home for the
extractor fixture tests proposed in #778's review (synthetic log in, golden
`rounds.tsv` diff out); when they land they will need only `bash`, no AWS or
GPU.
