# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Allowlist of routes permitted to have no authentication dependency at all.

``GET /trust/health`` shipped with no auth dependency and answered any anonymous caller behind
CloudFront with the roster of participating trusts and their liveness. Nothing caught it because
nothing asserted which routes are *allowed* to be open: each router adds its own ``Depends``, and
a route that forgets one is indistinguishable from a route that was meant to be public. This test
makes the open set explicit, so a new route with no auth fails CI naming itself, and removing auth
from an existing one is a deliberate edit to the list below rather than a silent omission.

"Authenticated" here means the route's dependency tree (walked transitively, via ``dependant``,
the same way ``test_mfa_gate`` checks the MFA wiring) reaches one of the four entry points:
``verify_token`` / ``verify_token_no_mfa`` (Cognito users), ``authenticate_trust`` (per-trust API
key) or ``authenticate_internal_service`` (the fl-server key). Docs routes are not ``APIRoute``
instances and are disabled outside development anyway, so they do not appear.

The set is compared exactly, in both directions, and the total route count is floored: a FastAPI
change to how included routers are exposed (the review saw an unpinned upgrade nest them as
``_IncludedRouter`` and defeat the MFA guard) would otherwise yield an empty set and a green run.
"""

from typing import Any

from fastapi.routing import APIRoute

from flip_api.auth.access_manager import authenticate_internal_service, authenticate_trust
from flip_api.auth.dependencies import verify_token, verify_token_no_mfa
from flip_api.main import app

AUTH_ENTRY_POINTS: tuple[Any, ...] = (
    verify_token,
    verify_token_no_mfa,
    authenticate_trust,
    authenticate_internal_service,
)

# Every route that may be reached without credentials, and why. Adding to this list is a
# security decision that belongs in the PR description, not a side effect of a new router.
PUBLIC_ROUTES = {
    "GET /api": "liveness — the bare API root",
    "GET /api/health": "liveness — the load-balancer / uptime probe target",
    "POST /api/users/access": "the access-request form is for people who do not have an account yet (FLIP#909 "
    "tracks bounding and throttling it; making it authenticated would defeat its purpose)",
}

# The app's full APIRoute count, not a round number below it. The largest single router carries
# 14 routes, so any slack here lets a whole router drop out of introspection while the surviving
# unauthenticated set still matches the allowlist exactly — the _IncludedRouter failure mode the
# floor exists to catch. Adding routes raises it; lowering it to pass is how the guard stops working.
MIN_API_ROUTES = 74


def _reaches_auth(route: APIRoute) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call in AUTH_ENTRY_POINTS:
            return True
        stack.extend(dep.dependencies)
    return False


def _route_key(route: APIRoute) -> str:
    return f"{','.join(sorted(route.methods or []))} {route.path}"


def test_only_allowlisted_routes_are_unauthenticated() -> None:
    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert len(api_routes) >= MIN_API_ROUTES, (
        f"only {len(api_routes)} APIRoutes found, expected at least {MIN_API_ROUTES} — either route "
        f"introspection has stopped seeing the app's routers, in which case an empty unauthenticated set "
        f"would be a false pass rather than a clean one, or routes were deliberately removed, in which "
        f"case lower MIN_API_ROUTES to the new count in the same commit"
    )

    unauthenticated = {_route_key(route) for route in api_routes if not _reaches_auth(route)}
    allowed = set(PUBLIC_ROUTES)

    assert unauthenticated - allowed == set(), (
        "route(s) reachable with no credentials that are not on the public allowlist: "
        f"{sorted(unauthenticated - allowed)}. Add an auth dependency (verify_token for users, "
        "authenticate_trust / authenticate_internal_service for machines), or — only if the route is "
        "genuinely public — add it to PUBLIC_ROUTES with the reason."
    )
    assert allowed - unauthenticated == set(), (
        "allowlisted route(s) are now authenticated or gone; prune PUBLIC_ROUTES so it stays exact: "
        f"{sorted(allowed - unauthenticated)}"
    )
