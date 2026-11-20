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
FROM ${BASE_REF} AS env

# The same switch fl-base takes. The sync below removes whatever it does not ask for, so it must ask
# for the dev group when the client image was built with it.
ARG LOCAL_DEV=false

# Where TotalSegmentator keeps its weights. Outside $HOME, which the dev composes re-point, and
# readable by any uid a kit runs the client as.
ENV TOTALSEG_HOME_DIR=/opt/totalsegmentator

# fl-base left /app holding flip-utils' pyproject.toml and uv.lock, so this needs nothing from the
# build context: it installs the packages the lock adds for the extra and changes no other. It runs
# as the image's own user, which owns the venv, so no chown is needed afterwards (a recursive one
# would copy the whole venv into this layer, see fl-base/Dockerfile).
RUN uv sync --frozen --no-cache --compile-bytecode --extra nvflare --extra full --extra segmentation $(if [ "$LOCAL_DEV" = "true" ]; then echo "--dev"; else echo "--no-dev"; fi)

# A job cannot download at a trust (FLIP#1206), so the weights are baked in, in a stage of their own:
# the final image takes only the directory, and what the download leaves elsewhere stays behind.
FROM env AS weights

# TotalSegmentator task names, space separated, and the digest of the directory they leave: a sha256
# over the sha256 of every file, the weights and config.json. Change both together; a failed check
# prints the digest it found, and an upstream change to either fails it.
ARG TOTALSEG_TASKS="total"
ARG TOTALSEG_WEIGHTS_SHA256=61836a548422fea4f49bdd33985f316ecd53e18f2ad9fc08a8f342ff18e819f9

USER root
# config.json is written first, so TotalSegmentator keeps it: its own would carry a random id and
# usage statistics on, and a job at a trust must not report to a third party. Every timestamp is
# then set to the epoch, so the same weights give the same layer and a trust pulls them once.
# A download is tried three times: the weights come from GitHub release assets, which now and then
# answer with a server error. A retry skips the sets already in place, and the digest check below
# refuses whatever a half-finished one left.
RUN mkdir -p ${TOTALSEG_HOME_DIR} && cd ${TOTALSEG_HOME_DIR} && \
    printf '{\n    "totalseg_id": "flip",\n    "send_usage_stats": false,\n    "prediction_counter": 0\n}\n' > config.json && \
    for task in ${TOTALSEG_TASKS}; do \
        for attempt in 1 2 3; do \
            totalseg_download_weights -t "$task" && break; \
            if [ "$attempt" = 3 ]; then exit 1; fi; \
            echo "Downloading the $task weights failed (attempt $attempt of 3); trying again." >&2; sleep 20; \
        done; \
    done && \
    found=$(find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1) && \
    if [ "$found" != "${TOTALSEG_WEIGHTS_SHA256}" ]; then echo "The digest of ${TOTALSEG_HOME_DIR} (weights and config.json) is $found, expected ${TOTALSEG_WEIGHTS_SHA256}" >&2; exit 1; fi && \
    chmod -R a+rX,go-w . && find . -exec touch -h -d @0 {} +

FROM env

# Owned by root and read-only to the job. TotalSegmentator rewrites config.json on every prediction
# when it can, with no lock, so two jobs in one client could corrupt it; it carries on when it cannot.
COPY --from=weights ${TOTALSEG_HOME_DIR} ${TOTALSEG_HOME_DIR}
# The weights stage stepped to root; the image runs as its own user.
USER flip
