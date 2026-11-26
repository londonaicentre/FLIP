#!/usr/bin/env bash
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
# Run the cohort drafting benchmark (FLIP#1556) in flip-api's venv, against this checkout's drafting code.
# Usage: run_bench.sh <bench.py args>, e.g. --model claude-sonnet-5-5 --out results/claude-sonnet-5-5.json
# Needs ANTHROPIC_API_KEY (--provider anthropic, the default) or AWS credentials (--provider bedrock), and
# trust/data-access-api's venv (`uv sync` there) for the trust validator; override with TRUST_PYTHON.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.."  # flip-api
exec uv run python "$HERE/bench.py" "$@"
