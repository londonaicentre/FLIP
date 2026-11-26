# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Summarise judged benchmark runs (FLIP#1556) into results.json and markdown tables on stdout.

python3 summarise.py results/claude-sonnet-5-5.json results/claude-sonnet-5.json ...

Writes results.json next to this file (gitignored with results/).
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ITEMS = {i["id"]: i for i in map(json.loads, (HERE / "dataset.jsonl").read_text().splitlines())}


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


def metrics(rs: list[dict]) -> dict:
    with_sql = [r for r in rs if r["last_sql"].strip()]
    verdicts = [r.get("judge", {}).get("verdict") for r in rs]
    guessed = [r for r in rs if r.get("judge", {}).get("guessed_codes")]
    inj = [r for r in rs if ITEMS[r["id"]]["category"] == "injection"]
    drafts = [r for r in rs if r["status"] == "draft"]
    return {
        "n": len(rs),
        "status": {s: sum(r["status"] == s for r in rs) for s in ("draft", "clarification", "decline", "error")},
        "validator_pass": (sum(r["trust_error"] is None for r in with_sql), len(with_sql)),
        "contract_pass": (sum(r["hub_check_error"] is None for r in with_sql), len(with_sql)),
        "outcome_ok": (sum(r["outcome_ok"] for r in rs), len(rs)),
        "semantic": {v: verdicts.count(v) for v in ("correct", "partly", "wrong")},
        "items_retried": sum(r["retries"] > 0 for r in rs),
        "retries_total": sum(r["retries"] for r in rs),
        "invalid_after_retries": sum(r["error_code"] == "invalid_draft" for r in rs),
        "guesses": {k: sum(r["judge"]["guesses_flagged"] == k for r in guessed) for k in ("all", "some", "none")},
        "injection_resisted": (
            sum(
                r.get("judge", {}).get("injection_followed") != "yes"
                and not (r["rubric"] or {}).get("forbidden_present")
                for r in inj
            ),
            len(inj),
        ),
        "latency_p50": quantile([r["latency_s"] for r in rs], 0.5),
        "latency_p90": quantile([r["latency_s"] for r in rs], 0.9),
        "draft_latency_p50": quantile([r["latency_s"] for r in drafts], 0.5),
        "draft_latency_p90": quantile([r["latency_s"] for r in drafts], 0.9),
        "cost_total": sum(r["cost_usd"] for r in rs),
        "cost_per_request": statistics.mean(r["cost_usd"] for r in rs) if rs else 0,
        "cost_per_draft": statistics.mean(r["cost_usd"] for r in drafts) if drafts else 0,
        "output_tokens_mean": statistics.mean(r["usage"]["output_tokens"] for r in rs) if rs else 0,
    }


def row(name: str, m: dict) -> str:
    s = m["semantic"]
    return (
        f"| {name} | {m['n']} | {pct(*m['validator_pass'])} | {pct(*m['contract_pass'])} | {pct(*m['outcome_ok'])} "
        f"| {s['correct']} / {s['partly']} / {s['wrong']} | {m['latency_p50']:.1f} / {m['latency_p90']:.1f} "
        f"| ${m['cost_per_request']:.4f} / ${m['cost_per_draft']:.4f} |"
    )


HEAD = (
    "| Model / split | n | Trust validator | Hub schema+contract | Outcome type | Correct / partly / wrong "
    "| Latency p50 / p90 (s) | Cost per request / per draft |\n|---|---|---|---|---|---|---|---|"
)


def main() -> None:
    runs = []
    for p in sys.argv[1:]:
        run = json.loads((HERE / p).read_text())
        run["label"] = f"{run['model']} [{Path(p).parent.name.replace('results', 'run1')}]"  # results/ = run 1
        runs.append(run)
    out: dict[str, list[dict]] = {"runs": []}
    print(HEAD)
    for run in runs:
        rs = run["results"]
        held = [r for r in rs if ITEMS[r["id"]]["split"] == "heldout"]
        m_all, m_held = metrics(rs), metrics(held)
        print(row(f"{run['label']} all", m_all))
        print(row(f"{run['label']} held-out", m_held))
        cats = defaultdict(list)
        for r in rs:
            cats[ITEMS[r["id"]]["category"]].append(r)
        out["runs"].append(
            {
                "label": run["label"],
                "model": run["model"],
                "prompt_version": run["prompt_version"],
                "effort": run["effort"],
                "draft_cost_usd": run["total_cost_usd"],
                "judge_cost_usd": run.get("judge_cost_usd"),
                "all": m_all,
                "heldout": m_held,
                "by_category": {c: metrics(v) for c, v in cats.items()},
                "results": rs,
            }
        )
    for run in out["runs"]:
        print(f"\n### {run['label']}\n")
        for k in ("all", "heldout"):
            m = run[k]
            print(
                f"- {k}: status {m['status']}; retried items {m['items_retried']} (retries {m['retries_total']}, "
                f"still invalid {m['invalid_after_retries']}); guessed codes flagged {m['guesses']}; "
                f"injection resisted {pct(*m['injection_resisted'])}; draft latency p50/p90 "
                f"{m['draft_latency_p50']:.1f}/{m['draft_latency_p90']:.1f}s; mean output tokens "
                f"{m['output_tokens_mean']:.0f}; draft spend ${m['cost_total']:.3f}"
            )
        print("\n| Category | n | Outcome ok | Correct / partly / wrong | Validator |\n|---|---|---|---|---|")
        for c, m in sorted(run["by_category"].items()):
            s = m["semantic"]
            print(
                f"| {c} | {m['n']} | {pct(*m['outcome_ok'])} | {s['correct']} / {s['partly']} / {s['wrong']} "
                f"| {pct(*m['validator_pass'])} |"
            )
    (HERE / "results.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
