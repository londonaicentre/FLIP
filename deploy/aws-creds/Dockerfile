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
# #568: boto3-based container-credential sidecar (see app.py).

FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" boto3
WORKDIR /app
COPY app.py .
# Non-privileged port so the container can run as the host (non-root) uid.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
