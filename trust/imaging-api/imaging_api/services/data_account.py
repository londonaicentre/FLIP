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
"""The XNAT data account: the identity the download and upload routes use (FLIP#2011).

imaging-api holds two XNAT identities. The provisioning account (``XNAT_SERVICE_USER``) is a site administrator
and does everything except the data routes. The data account (``XNAT_DATA_USER``) holds no site role and no
all-data group; it is a **Member** of each FLIP project, so a flaw on a route the fl-client reaches is bounded
by what a project member can do: read and upload within its own projects, never delete, never see another
project, never create users or query the PACS.

This module keeps that membership true:

- a project created from the hub gets the data account as a Member (``grant_data_access``);
- at startup, every FLIP project that predates the account gets it too (``run_backfill``), so an existing
  trust needs no re-seed and no XNAT reset.

FLIP's projects are the ones whose XNAT ID is a UUID (``to_create_project``); a project an administrator made
by hand is left alone.

The account's state is kept here for ``/health`` and for the data routes' error: they answer 503 naming the
account, rather than a 401 on every download, while it is missing.
"""

import asyncio
import enum
import uuid

import requests
from fastapi.concurrency import run_in_threadpool

from imaging_api.config import get_settings
from imaging_api.services.projects import get_all_projects
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

# How long the startup backfill waits between attempts while XNAT is not up yet or refuses the account.
BACKFILL_RETRY_SECONDS = 60


class DataAccountState(enum.StrEnum):
    """What ``/health`` reports as ``xnat_data_account``."""

    PENDING = "pending"  # the startup backfill has not finished a pass yet
    READY = "ready"  # the account logs in and is a Member of every FLIP project
    NOT_CONFIGURED = "not configured"  # XNAT_DATA_PASSWORD is empty: the kit predates the account
    MISSING = "refused by XNAT"  # no such user, or a password XNAT does not accept: xnat-init has not run since


_state = DataAccountState.PENDING


def state() -> DataAccountState:
    return DataAccountState.NOT_CONFIGURED if not get_settings().XNAT_DATA_PASSWORD else _state


def set_state(new: DataAccountState) -> None:
    global _state
    _state = new


class DataAccountMissingError(Exception):
    """XNAT has no user named ``XNAT_DATA_USER``."""


def remedy() -> str:
    """What an operator does about a missing account; part of every message that reports one."""
    user = get_settings().XNAT_DATA_USER
    if state() == DataAccountState.NOT_CONFIGURED:
        return (
            f"imaging-api has no password for the XNAT data account '{user}' (XNAT_DATA_PASSWORD is empty). "
            "Run the trust's upgrade command again (it adds XNAT_DATA_PASSWORD to the kit and creates the account), "
            "or make generate-xnat-credentials KIT=<CODE> and redeploy."
        )
    return (
        f"XNAT refused the data account '{user}'. It is created by configure-xnat.sh (xnat-init), which runs on "
        "every deploy: redeploy the trust, or run xnat-init, then restart imaging-api."
    )


def grant_data_access(project_id: str, headers: dict[str, str]) -> None:
    """Make the data account a Member of ``project_id``, on the provisioning account's ``headers``.

    Idempotent: XNAT answers 200 for a user who is already a Member. XNAT also answers 200, and adds nobody, for a
    user it does not have (seen on 1.10.2.1), so the answer's member list is what says it worked.

    Raises:
        DataAccountMissingError: The account is not among the project's Members afterwards (XNAT has no such user).
        Exception: Any other refusal.
    """
    user = get_settings().XNAT_DATA_USER
    response = requests.put(
        xnat_url("data", "projects", project_id, "users", "Members", user),
        headers=headers,
        timeout=XNAT_REQUEST_TIMEOUT,
    )
    if response.status_code == 404:
        raise DataAccountMissingError(f"XNAT has no user '{user}'")
    if response.status_code != 200:
        raise Exception(f"Adding '{user}' to project {project_id} failed: {response.status_code} - {response.text}")
    try:
        rows = response.json()["ResultSet"]["Result"]
        members = {row.get("login") for row in rows if row.get("displayname") == "Members"}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Exception(f"Adding '{user}' to project {project_id}: XNAT's answer is not a member list") from None
    if user not in members:
        raise DataAccountMissingError(f"XNAT has no user '{user}' (project {project_id} does not list it)")


def is_flip_project(project_id: str) -> bool:
    try:
        uuid.UUID(project_id)
    except ValueError:
        return False
    return True


def backfill(headers: dict[str, str]) -> int:
    """Add the data account to every FLIP project; returns how many there were.

    Raises:
        DataAccountMissingError: XNAT has no such user (nothing is retried until it has).
    """
    projects = [p for p in get_all_projects(headers) if is_flip_project(p.ID)]
    for project in projects:
        grant_data_access(project.ID, headers)
    return len(projects)


async def run_backfill() -> None:
    """At startup, until one pass succeeds: the account is then a Member of every FLIP project.

    New projects get the account when they are created, so one pass per start is enough.
    """
    from fastapi import HTTPException

    from imaging_api.utils.auth import get_xnat_auth_headers, xnat_data_token_factory

    if state() == DataAccountState.NOT_CONFIGURED:
        logger.error(remedy())
        return
    while True:
        try:
            headers = await run_in_threadpool(get_xnat_auth_headers)
            count = await run_in_threadpool(backfill, headers)
            # The account exists and has its memberships; check it logs in with the password this pod holds.
            await run_in_threadpool(xnat_data_token_factory.get_xnat_cookie)
        except DataAccountMissingError:
            set_state(DataAccountState.MISSING)
            logger.error(remedy())
        except HTTPException as e:
            if e.status_code not in (401, 403):
                logger.warning(f"XNAT data account login failed (HTTP {e.status_code}); trying again later")
            else:
                set_state(DataAccountState.MISSING)
                logger.error(remedy())
        except Exception as e:
            logger.warning(f"XNAT data account backfill failed ({type(e).__name__}: {e}); trying again later")
        else:
            set_state(DataAccountState.READY)
            logger.info(f"XNAT data account '{get_settings().XNAT_DATA_USER}' is a Member of {count} FLIP project(s)")
            return
        await asyncio.sleep(BACKFILL_RETRY_SECONDS)
