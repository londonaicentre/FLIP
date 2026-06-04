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

import logging
import os
from datetime import timezone

import boto3
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# uvicorn's error logger, so failure detail is visible via `docker logs aws-creds`.
logger = logging.getLogger("uvicorn.error")

app = FastAPI()

# Built once: get_credentials() returns a RefreshableCredentials object for SSO profiles,
# and get_frozen_credentials() refreshes it in-place as it nears expiry.
_session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"))


@app.get("/health")
def health() -> JSONResponse:
    # Liveness probe for the compose healthcheck. Deliberately does NOT touch boto3 — an expired
    # SSO token must not mark the sidecar unhealthy and block dependents from ever starting.
    return JSONResponse({"status": "ok"})


@app.get("/creds")
def creds() -> JSONResponse:
    try:
        credentials = _session.get_credentials()
        frozen = credentials.get_frozen_credentials()
    except Exception:  # e.g. SSO token expired / not logged in
        # Log the detail server-side (visible via `docker logs aws-creds` — e.g. to prompt
        # `aws sso login`), but don't echo the exception text back to the caller (CWE-209).
        logger.exception("Could not resolve AWS credentials")
        return JSONResponse({"error": "could not resolve credentials"}, status_code=500)

    # The SDK's container provider needs an Expiration to drive its re-poll cadence; without one
    # it treats the creds as static and never refreshes, so they'd go stale mid-run. Prefer a
    # public expiry_time if botocore exposes one, else fall back to the private attr, and format
    # inside a try so a type change / naive datetime fails loudly (logged 500) instead of
    # returning unrefreshable creds.
    expiry = getattr(credentials, "expiry_time", None) or getattr(credentials, "_expiry_time", None)
    try:
        expiration = expiry.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        logger.exception("Could not determine AWS credential expiry")
        return JSONResponse({"error": "could not determine credential expiry"}, status_code=500)

    return JSONResponse({
        "AccessKeyId": frozen.access_key,
        "SecretAccessKey": frozen.secret_key,
        "Token": frozen.token,
        # On the next poll we hand back boto3's freshly-refreshed creds.
        "Expiration": expiration,
    })
