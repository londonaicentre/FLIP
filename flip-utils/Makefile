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

# Test commands for flip-utils library — the same ruff → mypy → pytest sequence the other
# services' Makefiles run (flip-api, trust/*), plus a ruff format check, so a type or formatting
# error fails `make unit-test` the way it fails CI's flip-tests job.
lint_command = uv run ruff check . --fix
format_check_command = uv run ruff format --check .
mypy_command = uv run mypy . --ignore-missing-imports
test_coverage_command = uv run pytest -s -vv --cov=flip/ --cov-report=term-missing tests/

lint:
	$(lint_command)
format:
	uv run ruff format .
format-check:
	$(format_check_command)
mypy:
	$(mypy_command)
unit-test:
	$(lint_command) && $(format_check_command) && $(mypy_command) && $(test_coverage_command)

.PHONY: lint format format-check mypy unit-test
