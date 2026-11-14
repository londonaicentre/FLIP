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
"""Semantic benchmark for cohort SQL drafting (FLIP#1556): drafts every dataset item and checks it.

Runs flip-api's own drafting loop (``run_draft``: same prompt, schema, checks and retry rule) with a
model that sends exactly the request ``BedrockDraftModel`` sends. ``--provider anthropic`` calls the
Claude API (``ANTHROPIC_API_KEY``); ``--provider bedrock`` calls bedrock-runtime InvokeModel with the AWS
credentials in the environment, as the hub does. Run through ``run_bench.sh`` (flip-api's venv):

    ./run_bench.sh --model claude-sonnet-5-5 --out results/claude-sonnet-5-5.json

Then ``judge.py`` scores the drafts and ``summarise.py`` tabulates them. See README.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import anthropic

from flip_api.cohort_services.drafting import prompt
from flip_api.cohort_services.drafting.checks import _output_names, check_draft
from flip_api.cohort_services.drafting.model import ModelError, ModelReply
from flip_api.cohort_services.drafting.service import run_draft
from flip_api.cohort_services.submit_cohort_query import validate_query as hub_parse
from flip_api.config import get_settings

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
# $ per million tokens at Claude API list prices: input, output, cache write (5 min), cache read. Bedrock
# prices differ; a Bedrock run is costed at these rates as an estimate.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 2.5, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
}


def cost(model: str, usage: dict[str, int]) -> float:
    i, o, cw, cr = PRICES[model.rsplit("anthropic.", 1)[-1]]
    return (
        usage["input_tokens"] * i
        + usage["output_tokens"] * o
        + usage["cache_creation_input_tokens"] * cw
        + usage["cache_read_input_tokens"] * cr
    ) / 1e6


class BenchModel:
    """The request BedrockDraftModel.create sends, against any Messages client; records usage and replies."""

    def __init__(self, client: Any, model: str) -> None:
        settings = get_settings()
        self.name, self._client, self._model = "bench", client, model
        self._max_tokens, self._effort = settings.COHORT_DRAFT_MAX_TOKENS, settings.COHORT_DRAFT_EFFORT
        self.calls: list[dict[str, Any]] = []  # per item, reset by the caller

    def create(self, system: str, messages: list[dict[str, Any]]) -> ModelReply:
        try:
            r = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": prompt.OUTPUT_SCHEMA},
                },
            )
        except anthropic.APIError as e:
            self.calls.append({"error": f"{type(e).__name__}: {str(e)[:200]}"})
            raise ModelError(type(e).__name__) from e
        text = next((b.text for b in r.content if b.type == "text"), "")
        u = r.usage
        self.calls.append(
            {
                "stop_reason": r.stop_reason,
                "text": text,
                "usage": {
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
                    "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                },
            }
        )
        category = r.stop_details.category if r.stop_reason == "refusal" and r.stop_details else None
        return ModelReply(r.stop_reason, text, r.content, u.input_tokens, u.output_tokens, category)


def trust_validate(sqls: list[str]) -> list[str | None]:
    """The trust's validate_query, run in trust/data-access-api's venv (see trust_validate.py)."""
    trust = REPO / "trust"
    python = os.environ.get("TRUST_PYTHON", str(trust / "data-access-api/.venv/bin/python"))
    out = subprocess.run(
        [python, str(HERE / "trust_validate.py")],
        input=json.dumps(sqls),
        capture_output=True,
        text=True,
        check=True,
        env=os.environ | {"PYTHONPATH": f"{trust / 'data-access-api'}:{trust / 'observability'}"},
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", sql)).lower()


def rubric_hits(sql: str, expected: dict) -> dict[str, list[str]]:
    """Missing must_filter_on terms ("a|b" = any of), missing must_return columns, present must_not terms."""
    s = _norm(sql)
    missing = [t for t in expected["must_filter_on"] if not any(a.lower() in s for a in t.split("|"))]
    try:
        names = _output_names(hub_parse(sql)) or set()
    except ValueError:
        names = set()
    no_return = [c for c in expected["must_return"] if c not in names]
    present = [t for t in expected["must_not"] if t.lower() in s]
    return {"missing_filters": missing, "missing_returns": no_return, "forbidden_present": present}


def run_item(model: BenchModel, item: dict) -> dict:
    model.calls = []
    imaging = item["project_shape"] == "imaging"
    t0 = time.monotonic()
    out = run_draft(model, item["description"], [], imaging)
    latency = time.monotonic() - t0
    calls = list(model.calls)
    # The last SQL the model wrote, also when every attempt failed the hub's checks.
    last_sql = out.sql
    if not last_sql and calls and calls[-1].get("text"):
        try:
            last_sql = json.loads(calls[-1]["text"]).get("sql", "")
        except json.JSONDecodeError:
            pass
    usage = {
        k: sum(c.get("usage", {}).get(k, 0) for c in calls)
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    }
    return {
        "id": item["id"],
        "status": out.status,
        "error_code": out.error_code,
        "sql": out.sql,
        "last_sql": last_sql,
        "explanation": out.explanation,
        "assumptions": out.assumptions,
        "concepts_used": out.concepts_used,
        "question": out.question,
        "options": out.options,
        "retries": out.retries,
        "model_calls": out.model_calls,
        "refusal_category": out.refusal_category,
        "latency_s": round(latency, 2),
        "usage": usage,
        "call_errors": [c["error"] for c in calls if "error" in c],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", choices=["anthropic", "bedrock"], default="anthropic")
    ap.add_argument("--split", choices=["all", "dev", "heldout"], default="all")
    ap.add_argument("--ids", help="comma-separated item ids")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    items = [json.loads(line) for line in (HERE / "dataset.jsonl").read_text().splitlines()]
    items = [i for i in items if args.split == "all" or i["split"] == args.split]
    if args.ids:
        items = [i for i in items if i["id"] in args.ids.split(",")]
    client: anthropic.Anthropic | anthropic.AnthropicBedrock
    if args.provider == "bedrock":
        client = anthropic.AnthropicBedrock(aws_region=get_settings().COHORT_DRAFT_BEDROCK_REGION)
    else:
        client = anthropic.Anthropic(max_retries=4, timeout=get_settings().COHORT_DRAFT_TIMEOUT_SECONDS * 2)

    def one(item: dict) -> dict:
        return run_item(BenchModel(client, args.model), item)

    # The first item alone writes the prompt cache; the rest read it.
    results = [one(items[0])]
    with ThreadPoolExecutor(args.workers) as pool:
        results += list(pool.map(one, items[1:]))

    by_id = {i["id"]: i for i in items}
    trust = trust_validate([r["last_sql"] for r in results])
    for r, trust_error in zip(results, trust):
        item = by_id[r["id"]]
        r["cost_usd"] = round(cost(args.model, r["usage"]), 5)
        r["trust_error"] = trust_error if r["last_sql"].strip() else None
        r["hub_check_error"] = check_draft(r["last_sql"], item["project_shape"] == "imaging") if r["last_sql"] else None
        exp = item["expected"]
        r["outcome_ok"] = r["status"] in exp.get("acceptable_outcomes", [exp["expected_outcome"]])
        r["rubric"] = rubric_hits(r["last_sql"], exp) if r["last_sql"] else None
    total = sum(r["cost_usd"] for r in results)
    out_path = HERE / args.out  # relative to this file, not to the working directory
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "model": args.model,
                "provider": args.provider,
                "prompt_version": prompt.PROMPT_VERSION,
                "effort": get_settings().COHORT_DRAFT_EFFORT,
                "max_tokens": get_settings().COHORT_DRAFT_MAX_TOKENS,
                "total_cost_usd": round(total, 4),
                "results": results,
            },
            indent=2,
        )
    )
    print(f"{args.model}: {len(results)} items, ${total:.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
