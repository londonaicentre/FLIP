# Copyright (c) 2026 Flower Labs GmbH
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

FROM flwr/base:${FLWR_VERSION:-1.26.1-py3.12-ubuntu24.04}

WORKDIR /app

# Copy only what we need for dependency install (keeps build cache efficient)
COPY --chown=app:app pyproject.toml .

# Only the non-Flower dependencies are needed.
# Remove the flwr[simulation] dependency from pyproject.toml before installing, since it's only needed for the 
# simulation service, and would pull in extra dependencies that aren't needed for the API service.
RUN sed -i 's/.*flwr\[simulation\].*//' pyproject.toml \
  && python -m pip install -U --no-cache-dir .
