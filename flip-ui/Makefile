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

ifeq ($(PROD),true)
MAIN_ENV_FILE=../.env.production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=../.env.stag
else
MAIN_ENV_FILE=../.env.development
endif

$(info Using MAIN_ENV_FILE: $(MAIN_ENV_FILE))

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

docker_command = docker compose -f ../deploy/compose.development.yml

unit_test:
	$(docker_command) run --rm --remove-orphans --name flip-ui-test flip-ui npm run test:unit:update
shell:
	$(docker_command) run --rm --remove-orphans --name flip-ui-shell flip-ui sh