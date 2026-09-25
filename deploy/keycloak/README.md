# Keycloak: the local identity provider

`flip-realm.json` is the FLIP development realm (FLIP#919): what the Cognito user
pool is in staging and production. The `keycloak` service in
`deploy/compose.development.yml` imports it at every boot (`start-dev --import-realm`)
and keeps no volume, so this file in git is the whole state of the dev identity
provider. flip-api verifies its tokens through the same generic OIDC verifier that
verifies Cognito's, and administers users through its Admin REST API
(`flip-api/src/flip_api/auth/identity/keycloak.py`); the UI signs in with the OIDC
password grant, so the login form is the same under both backends.

## What the realm carries

| Item | Value | Why |
|------|-------|-----|
| Realm | `flip`, email as username, TOTP-only OTP policy | Mirrors the Cognito pool: no custom attributes, no groups; roles live in the FLIP database |
| Client `flip-ui` | public, direct access grants on, redirect URIs `http://localhost:44350`–`44359` + `http://localhost:${UI_PORT}`, audience mapper `flip-api` | The browser client. Its redirect URIs double as flip-api's CORS allowlist (`IdentityProvider.allowed_origins`), the role Cognito's callback URLs play elsewhere |
| Client `flip-api-admin` | confidential, service account with `manage-users view-users query-users view-clients` | What flip-api authenticates to the Admin REST API as; its client secret is `KEYCLOAK_ADMIN_CLIENT_SECRET` <!-- pragma: allowlist secret --> |
| Users | the well-known dev identities from `flip_api/utils/constants.py`, fixed ids, password `${ADMIN_USER_PASSWORD}` | Fixed ids keep the FLIP database's role rows valid across a recreate. Demo-video users are not here: `make demo-users` creates them |
| User profile | Keycloak's default declarative profile with `firstName`/`lastName` optional (the `UserProfileProvider` component; its config is a JSON string) | FLIP keeps names in its own database and creates users with an email only. With the default required names, Keycloak's `VERIFY_PROFILE` makes the password grant answer "Account is not fully set up" for every user registered from the Admin Area, even after they set a password |

`${VAR}` placeholders resolve from the container's environment, which the compose
service sets from the hub env file (`ADMIN_USER_PASSWORD`, `KEYCLOAK_ADMIN_CLIENT_SECRET`,
`UI_PORT`). Keycloak rejects unknown top-level keys, so the file carries no comments.

## Editing it

`--import-realm` skips a realm that already exists, so an edit only applies to a fresh
container: `make reset-keycloak` (or `make down && make up`). Users registered from the
Admin Area since the last import live only in that container and go with it.

The admin console is `http://localhost:${KEYCLOAK_PORT:-8180}/admin` (`admin`/`admin`
unless `KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD` are set). It is where a
developer completes what the password grant cannot do in-app: enrol or clear a TOTP
credential, or reset a password. (A user registered from the Admin Area needs none of
that: dev has no mail server, so they get `ADMIN_USER_PASSWORD` as a temporary password
and choose a new one at first sign-in.)

Never load this realm anywhere but a laptop: every credential in it is shared dev
state, and `ProdSettings` refuses `AUTH_BACKEND=keycloak` at boot.
