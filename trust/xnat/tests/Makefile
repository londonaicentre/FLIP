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

.PHONY: lint mypy pytest test unit_test local_test

lint_command = uv run ruff check .
mypy_command = uv run mypy . --ignore-missing-imports
pytest_command = uv run pytest --tb=short

lint:
	$(lint_command)

mypy:
	$(mypy_command)

pytest:
	$(pytest_command)

test: lint mypy pytest

unit_test: test
local_test: test
