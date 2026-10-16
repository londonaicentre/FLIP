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

"""AWS-free hub smoke: sign in through the local Keycloak and reach the hub as admin (FLIP#919).

Run by .github/workflows/local_auth_smoke.yml against a hub booted with
``AUTH_BACKEND=keycloak`` and no AWS credentials at all. It does what the
browser does — the OIDC password grant against the *public* Keycloak URL —
then proves the token is accepted by flip-api, that the boot seed granted
the admin role through the provider, that the usual rejections still hold,
and that refresh and logout work. Standard library only, on purpose: the
runner needs no virtualenv to run it.

    python3 tests/local_auth_smoke.py --api-url http://localhost:8080/api \\
        --keycloak-url http://localhost:8180 --username <ADMIN_EMAIL_1> --password <ADMIN_USER_PASSWORD>
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class SmokeFailure(Exception):
    """A step did not behave as the hub contract requires."""


def _request(
    method: str, url: str, *, headers: dict[str, str] | None = None, form: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if form is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, body.decode(errors="replace")


def _expect(status: int, wanted: int, what: str, body: Any) -> None:
    if status != wanted:
        raise SmokeFailure(f"{what}: expected HTTP {wanted}, got {status}: {body}")
    print(f"  ✅ {what} → {status}")


def _claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(payload)))


def _token_endpoint(keycloak_url: str, realm: str) -> str:
    return f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"


def run(args: argparse.Namespace) -> None:
    token_url = _token_endpoint(args.keycloak_url, args.realm)
    issuer = f"{args.keycloak_url.rstrip('/')}/realms/{args.realm}"
    api = args.api_url.rstrip("/")

    print("🔐 Password grant against the public Keycloak URL")
    status, tokens = _request(
        "POST",
        token_url,
        form={
            "grant_type": "password",
            "client_id": args.client_id,
            "username": args.username,
            "password": args.password,
            "scope": "openid",
        },
    )
    _expect(status, 200, "password grant", tokens)
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    print("🔎 The token carries the issuer flip-api verifies (KC_HOSTNAME pins it)")
    claims = _claims(access)
    if claims.get("iss") != issuer:
        raise SmokeFailure(
            f"iss is {claims.get('iss')!r}, expected {issuer!r} — check KC_HOSTNAME / KEYCLOAK_PUBLIC_URL"
        )
    aud = claims.get("aud")
    audiences: list[Any] = aud if isinstance(aud, list) else [aud]
    if args.audience not in audiences:
        raise SmokeFailure(f"aud {aud!r} does not contain {args.audience!r} — check the realm's audience mapper")
    print(f"  ✅ iss={claims['iss']} aud={aud}")

    bearer = {"Authorization": f"Bearer {access}"}

    print("👤 The hub accepts the token and resolves the caller through the provider")
    status, me = _request("GET", f"{api}/users/me", headers=bearer)
    _expect(status, 200, "GET /users/me", me)
    if me.get("email", "").lower() != args.username.lower():
        raise SmokeFailure(f"/users/me returned {me.get('email')!r}, expected {args.username!r}")
    if me.get("id") != claims.get("sub"):
        raise SmokeFailure(f"/users/me id {me.get('id')!r} differs from the token sub {claims.get('sub')!r}")

    print("🛡️  Boot seeding granted the admin role to the seeded user")
    status, perms = _request("GET", f"{api}/users/{claims['sub']}/permissions", headers=bearer)
    _expect(status, 200, "GET /users/{sub}/permissions", perms)
    if args.expect_permission not in perms.get("permissions", []):
        raise SmokeFailure(f"{args.expect_permission!r} missing from {perms.get('permissions')!r}")

    print("🚫 The usual rejections still hold")
    status, body = _request("GET", f"{api}/users/me")
    # FastAPI's HTTPBearer answers 403 when the header is absent; 401 is what a bad token gets.
    if status not in (401, 403):
        raise SmokeFailure(f"no token: expected HTTP 401/403, got {status}: {body}")
    print(f"  ✅ no token → {status}")
    tampered = access[:-2] + ("AA" if not access.endswith("AA") else "BB")
    status, body = _request("GET", f"{api}/users/me", headers={"Authorization": f"Bearer {tampered}"})
    _expect(status, 401, "tampered signature", body)
    status, body = _request("GET", f"{api}/users/me", headers={"Authorization": f"Bearer {tokens['id_token']}"})
    _expect(status, 401, "ID token as bearer", body)
    status, body = _request(
        "POST",
        token_url,
        form={"grant_type": "password", "client_id": args.client_id, "username": args.username, "password": "wrong"},
    )
    # Keycloak answers invalid_grant with 400 (older releases used 401); the error code is the contract.
    if status not in (400, 401) or not (isinstance(body, dict) and body.get("error") == "invalid_grant"):
        raise SmokeFailure(f"wrong password: expected invalid_grant, got {status}: {body}")
    print(f"  ✅ wrong password → {status} invalid_grant")

    print("🔄 Refresh, then logout revokes the session")
    status, refreshed = _request(
        "POST", token_url, form={"grant_type": "refresh_token", "client_id": args.client_id, "refresh_token": refresh}
    )
    _expect(status, 200, "refresh grant", refreshed)
    if refreshed["access_token"] == access:
        raise SmokeFailure("refresh returned the same access token")
    status, body = _request("GET", f"{api}/users/me", headers={"Authorization": f"Bearer {refreshed['access_token']}"})
    _expect(status, 200, "GET /users/me with the refreshed token", body)
    status, body = _request(
        "POST",
        f"{args.keycloak_url.rstrip('/')}/realms/{args.realm}/protocol/openid-connect/logout",
        form={"client_id": args.client_id, "refresh_token": refreshed["refresh_token"]},
    )
    _expect(status, 204, "logout", body)
    status, body = _request(
        "POST",
        token_url,
        form={"grant_type": "refresh_token", "client_id": args.client_id, "refresh_token": refreshed["refresh_token"]},
    )
    _expect(status, 400, "refresh after logout", body)

    print("\n🎉 Local auth smoke passed: the hub signs users in with no AWS account.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://localhost:8080/api")
    parser.add_argument("--keycloak-url", default="http://localhost:8180", help="the PUBLIC URL (token iss)")
    parser.add_argument("--realm", default="flip")
    parser.add_argument("--client-id", default="flip-ui")
    parser.add_argument("--audience", default="flip-api")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--expect-permission", default="CanAccessAdminPanel")
    args = parser.parse_args(argv)
    try:
        run(args)
    except SmokeFailure as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"\n❌ connection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
