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

# TotalSegmentator's weights and nothing else. The FL client image copies this directory in, so its
# own build downloads nothing from GitHub's release assets and cannot fail on them (FLIP#1945).
FROM python:3.13-slim-bookworm AS download

# The version whose task-to-weights table decides what `-t` fetches; keep it at flip-utils' lock.
ARG TOTALSEG_VERSION=2.18.0
# Task names, space separated, and the digest of the directory they leave: a sha256 over the sha256
# of every file, the weights and config.json. Change both together; a failed check prints the digest
# it found. Only tasks whose weights we may redistribute: `total` is Apache-2.0.
ARG TOTALSEG_TASKS="total"
ARG TOTALSEG_WEIGHTS_SHA256=61836a548422fea4f49bdd33985f316ecd53e18f2ad9fc08a8f342ff18e819f9
ENV TOTALSEG_HOME_DIR=/opt/totalsegmentator

# CPU torch: this stage only downloads, and it is thrown away.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu "totalsegmentator==${TOTALSEG_VERSION}" && \
    cp "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/totalsegmentator-${TOTALSEG_VERSION}.dist-info/licenses/LICENSE" /LICENSE

# config.json is written first, so TotalSegmentator keeps it: its own would carry a random id and
# usage statistics on, and a job at a trust must not report to a third party. A download is tried
# three times, because the release assets now and then answer with a server error; a retry skips
# the sets already in place, and the digest check refuses whatever a half-finished one left. Every
# timestamp is then set to the epoch, so the same weights give the same layer.
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

FROM scratch
COPY --from=download /LICENSE /LICENSE
COPY --from=download /opt/totalsegmentator /opt/totalsegmentator
