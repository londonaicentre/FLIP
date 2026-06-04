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
# #568: minimal boto3 container-credential provider. Resolves the host's
# (modern sso_session) profile via boto3 — which the Go ecs-local-endpoints tool can't — and
# serves the ECS creds JSON at /creds. boto3's RefreshableCredentials auto-refresh the role
# creds from the SSO token, so FL containers fetch fresh, uid-agnostic creds over HTTP and
# need no SSO mount, no root.

import os
from datetime import timezone

import boto3
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

# Built once: get_credentials() returns a RefreshableCredentials object for SSO profiles,
# and get_frozen_credentials() refreshes it in-place as it nears expiry.
_session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"))


@app.get("/creds")
def creds() -> JSONResponse:
    try:
        credentials = _session.get_credentials()
        frozen = credentials.get_frozen_credentials()
    except Exception as exc:  # e.g. SSO token expired / not logged in
        return JSONResponse({"error": f"could not resolve credentials: {exc}"}, status_code=500)

    expiry = getattr(credentials, "_expiry_time", None)
    return JSONResponse({
        "AccessKeyId": frozen.access_key,
        "SecretAccessKey": frozen.secret_key,
        "Token": frozen.token,
        # Expiration drives the SDK's re-poll cadence; on the next call we hand back
        # boto3's freshly-refreshed creds.
        "Expiration": expiry.astimezone(timezone.utc).isoformat() if expiry else None,
    })
