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

ARG BASE_REF=flower-fl-base:dev
FROM ${BASE_REF}

# The one path the SuperLink writes on ECS, where HOME and WORKING_DIR point at /tmp and the root
# filesystem is read-only (FLIP#1795). Fargate mounts an empty volume root-owned 0755 unless the
# image declares the path a VOLUME, which keeps /tmp's 1777.
VOLUME ["/tmp"]
ENTRYPOINT ["flower-superlink"]
