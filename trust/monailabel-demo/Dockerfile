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
# Recorder for the MONAI Label demo video (record_demo.py). The mcr image ships the browsers but
# not the Python package, so it is pinned here to the same release; ffmpeg encodes the frames; numpy,
# scipy and SimpleITK choose the SAM clicks (choose_clicks.py).
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages playwright==1.63.0 \
       numpy==2.5.4 scipy==1.18.1 simpleitk==2.5.6
WORKDIR /demo
COPY choose_clicks.py make_gifs.py record_demo.py ./
ENTRYPOINT ["python3", "/demo/record_demo.py"]
