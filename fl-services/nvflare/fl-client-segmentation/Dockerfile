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

# The segmentation FL environment (FLIP#32): the FL client image plus flip-utils' `segmentation`
# extra and TotalSegmentator's weights. Built on the client image, so everything a trust has already
# reviewed is unchanged and this image differs from it by the two layers below.
ARG BASE_REF=ghcr.io/londonaicentre/flare-fl-client:latest
FROM ${BASE_REF}

# Where TotalSegmentator keeps its weights. Outside $HOME, which the dev composes re-point, and
# readable by any uid a kit runs the client as.
ENV TOTALSEG_HOME_DIR=/opt/totalsegmentator

# fl-base left /app holding flip-utils' pyproject.toml and uv.lock, so this needs nothing from the
# build context: it installs the packages the lock adds for the extra and changes no other. It runs
# as the image's own user, which owns the venv, so no chown is needed afterwards (a recursive one
# would copy the whole venv into this layer, see fl-base/Dockerfile).
RUN uv sync --frozen --no-cache --compile-bytecode --extra nvflare --extra full --extra segmentation --no-dev

# A job cannot download at a trust (FLIP#1206), so the weights are baked in. Tasks are TotalSegmentator
# task names, space separated. The download also writes TotalSegmentator's config.json, with its usage
# statistics on: they are turned off here, because a job at a trust must not report to a third party.
ARG TOTALSEG_TASKS="total"
USER root
RUN mkdir -p ${TOTALSEG_HOME_DIR} && chown flip:flip ${TOTALSEG_HOME_DIR}
USER flip
RUN for task in ${TOTALSEG_TASKS}; do totalseg_download_weights -t "$task" || exit 1; done && \
    python -c "import json, os; p = os.path.join(os.environ['TOTALSEG_HOME_DIR'], 'config.json'); c = json.load(open(p)); c['send_usage_stats'] = False; json.dump(c, open(p, 'w'), indent=4)" && \
    chmod -R a+rX ${TOTALSEG_HOME_DIR}
