# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

.PHONY: local_lint local_mypy local_test

test_coverage_html_command = uv run pytest --tb=short --disable-warnings --cov=fl_api/ --cov-report=html --cov-report=term-missing
lint_command = uv run ruff check . --fix
mypy_command = uv run mypy . --ignore-missing-imports

local_lint:
	$(lint_command)
local_mypy:
	$(mypy_command)
local_test:
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command)