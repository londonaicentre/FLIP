<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Data Access API

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Data Access API CI](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_data_access_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_data_access_api.yml)
[![data-access-api](https://ghcr-badge.egpl.dev/londonaicentre/data-access-api/latest_tag?trim=major&label=data-access-api)](https://github.com/londonaicentre/FLIP/pkgs/container/data-access-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=data-access-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **data-access-api** executes researcher-supplied SQL queries against the Trust's local OMOP database and returns
aggregated statistics and dataframes. It is an internal Trust-side service called by the
[trust-api](../trust-api/) (`/cohort`), [imaging-api](../imaging-api/) (`/cohort/accession-ids`),
and the [`flip` Python package](https://github.com/londonaicentre/FLIP/tree/develop/flip-utils/flip)
shipped to fl-client containers (`/cohort/dataframe`, via `flip.get_dataframe(...)`). All callers
must authenticate with the trust-internal service key — see [Authentication](#authentication)
below.

## Role in the FLIP Platform

When a researcher submits a cohort query for a federated learning study, the [trust-api](../trust-api/) delegates
the query execution to the data-access-api. The service:

1. Receives a SQL query (restricted to `SELECT` operations on the OMOP schema)
2. Executes it against the local [OMOP database](../omop-db/)
3. Returns aggregated results (counts, statistics) — **no individual patient records are returned**

This service is not directly accessible from outside the Trust network.

## Deployment

The data-access-api starts as part of the Trust-side stack:

```bash
make up-trusts
```

or the full platform:

```bash
make up
```

It requires the [OMOP database](../omop-db/) to be running and populated with data.

## Configuration

Key environment variables (set in [`.env.development.example`](../../.env.development.example)):

| Variable | Description |
| --- | --- |
| `OMOP_DB_SERVICE_NAME` | Docker service name or hostname of the OMOP database |
| `OMOP_DB_PORT` | Port of the OMOP database |
| `DATA_ACCESS_POSTGRES_USER` | PostgreSQL username for OMOP database access |
| `DATA_ACCESS_POSTGRES_PASSWORD` | PostgreSQL password for OMOP database access |
| `OMOP_POSTGRES_DB` | Name of the OMOP PostgreSQL database |
| `AES_KEY_BASE64` | AES encryption key for decrypting project identifiers |
| `TRUST_INTERNAL_SERVICE_KEY_HEADER` | Header name for trust-internal service auth (default `X-Trust-Internal-Service-Key`) |
| `TRUST_INTERNAL_SERVICE_KEY` | Per-trust plaintext key. Required on every `/cohort` request. |

## Authentication

data-access-api executes SQL against the OMOP database using a service account. To prevent any
container on the trust Docker network — or any operator with SSM port-forward access — from
running unrestricted queries against OMOP, every route under `/cohort` requires callers to send
`TRUST_INTERNAL_SERVICE_KEY` in the configured header. data-access-api compares the header
against its own copy of the same per-trust key with a constant-time compare. `/health` stays
unauthenticated so liveness probes keep working.

Callers in this repo: trust-api (`/cohort`) and imaging-api (`/cohort/accession-ids`). The fl-client
container calls `/cohort/dataframe` indirectly: user training code calls `flip.get_dataframe(...)`
from the [`flip` Python package](https://github.com/londonaicentre/FLIP/tree/develop/flip-utils/flip)
(consumed by both NVFLARE and Flower fl-client / fl-server images), and that package reads
`TRUST_INTERNAL_SERVICE_KEY` from `os.environ` and adds the header to its HTTP request. Tutorials
and user-uploaded `client_app.py` / `server_app.py` do not deal with the header directly.

Each trust has a distinct key. A trust's `TRUST_INTERNAL_SERVICE_KEY` is minted by `register_trust`
(`make register-trusts`) and written into that trust's kit file (`trust/.env.<CODE>.<env>`), which
`trust/Makefile` `-include`s so every trust-internal container inherits it.

For the threat model, see the **Trust-internal Service Authentication** section in
[`CLAUDE.md`](../../CLAUDE.md).

## Cohort query validation

A cohort query passes three layers before it runs, and they do **different jobs**. They are not
copies of each other and must not be made into copies:

| Layer | Job | Authority? |
| --- | --- | --- |
| flip-ui cohort form | Required-field validation only — "you typed something" | No |
| flip-api `submit_cohort_query.validate_query` | Fast-feedback validity pre-check before fan-out | No |
| data-access-api `services/cohort.validate_query` | Decides what may actually run against OMOP | **Yes** |

### This service is the authority

`validate_query` in [`services/cohort.py`](data_access_api/services/cohort.py) is the security
boundary, and it is deliberately self-sufficient. A trust holds patient data and the central hub
is a separate administrative domain; the trust must stay safe regardless of what the hub did or
did not check, because a compromised, misconfigured, or simply out-of-date hub must not be able to
widen what a trust will execute. Nothing here may be relaxed on the assumption that the hub
filtered first.

It performs a single parse-validate-emit pass and enforces:

1. A maximum query length (cheap DoS guard, before the parser allocates an AST).
2. Exactly one non-empty statement (defeats query stacking and stray semicolons).
3. A SELECT-shaped top-level statement (`SELECT`/`UNION`/`INTERSECT`/`EXCEPT`).
4. No `INSERT`/`UPDATE`/`DELETE`/`MERGE` node anywhere in the tree. Rule 3 only inspects the
   top-level node, and Postgres allows a writable CTE — `WITH x AS (DELETE FROM t RETURNING *)
   SELECT * FROM x` parses as a `Select` and would otherwise pass. The read-only role rejects
   the write regardless, so this is defence in depth rather than the only barrier.
5. Schema-qualified tables limited to `omop` (blocks `information_schema` / `pg_catalog`
   enumeration, which Postgres exposes to role `public` by default).
6. Literal-integer `LIMIT`/`OFFSET` (defeats blind extraction that makes the row count a function
   of a character value and reads it back through the cohort-size response).

It then returns the query **re-emitted from the AST it just checked**. Callers pass that string to
the engine, never the caller's original — so what reaches Postgres is generated from a validated
tree. Underneath all of this the service connects as `data_analyst_reader`, a role with `SELECT`
only and `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`CREATE` revoked, so DDL and DML are refused by
Postgres itself. That is why `validate_query` does not keyword-filter for `DROP` and friends.

Emitting from `validate_query` rather than from a second helper is deliberate: it keeps one parse
and one policy, so there is no second copy of the single-statement and SELECT-shape rules to drift
out of step.

### The hub check is fast feedback, not security

`validate_query` in `flip_api/cohort_services/submit_cohort_query.py` runs before the hub fans a
query out. Because this service is self-sufficient, anything the hub enforced for safety would
have to be enforced here anyway — so duplicating these rules on the hub would buy no security,
only two copies of one policy to keep in sync.

What it buys instead is **fast feedback**. Submitting a cohort query fans it out to every
registered trust as an encrypted task, each validating asynchronously and reporting back. Without
a pre-check, a researcher who typos their SQL waits for that entire round-trip, across N trusts,
to find out. Catching "this cannot succeed anywhere" while the request is still in hand turns a
multi-minute fan-out into an immediate 400.

So the hub check is deliberately *weaker*, and only rejects what every trust would reject too:
unparseable input, multiple statements, non-SELECT statements, and over-length queries. It
intentionally does **not** enforce trust-local policy — the `omop` schema pin, the literal-`LIMIT`
rule, the writable-CTE rejection, the read-only role, or the cohort-size threshold — because those depend on facts the hub has
no authority over and which may legitimately differ per trust.

That asymmetry makes drift harmless in the direction that matters. A hub lagging behind a trust
merely wastes a fan-out on a query the trust then refuses; it is never a bypass. A hub that got
*ahead* would reject something a trust would have allowed, which surfaces immediately as a usability
regression. Neither is a security failure — which is a far cheaper invariant to maintain than
"these two must match".

### No keyword denylists anywhere

The hub check does not use a keyword denylist, and neither does the UI. Both previously carried
the same regex banning substrings including `substring` — which blocked every legitimate use of
the standard `SUBSTRING()` function while stopping nothing an attacker would actually do. The
blind-extraction technique it was aimed at is defeated properly here by rule 5 above, and DDL/DML
is refused by the read-only database role.

Two copies of one denylist across two services was also exactly the drift hazard this layering
exists to avoid: a rule that is not authoritative anywhere still had to be maintained in both
places. The UI now validates only that a query was entered, and reports SQL problems from the
hub's response.

### Row-level data and the disclosure threshold

`/cohort` returns aggregate statistics and suppresses any count below `COHORT_QUERY_THRESHOLD`,
including a genuine zero, so the response cannot reveal that at least one patient matched.

`/cohort/dataframe` is the training-data path — user FL code reaches it through
`flip.get_dataframe(...)` — so it necessarily returns row-level records; a model trains on rows.
The data stays inside the trust and only model updates leave it. It applies the same
`COHORT_QUERY_THRESHOLD`: a cohort below the threshold is refused with a 403 whose message is
identical for zero rows and for threshold-minus-one, so the refusal cannot be used as an oracle.
Such a cohort has no training value anyway.

There is deliberately no column allowlist on `/cohort/dataframe`. `accession_id` is load-bearing —
it is how returned rows join to the imaging studies pulled into XNAT — and shipped tutorials
legitimately select `*`, so a column filter would break every FL app while a caller could trivially
alias around it. Column-level minimisation belongs in the cohort query a project submits and in
project approval, not at this layer.

`/cohort/accession-ids` is the minimal-disclosure endpoint: it wraps the caller's validated query
so only the `accession_id` column can cross the boundary. It applies the same threshold and the
same fixed refusal text, because accession IDs are still row-level identifiers — and they are the
pointer set into the imaging data, deciding whose studies get pulled into XNAT where project
members view them. Releasing them for a cohort of three is the disclosure the threshold exists to
prevent, whatever the column count.

The trust applies that check itself rather than relying on the hub. The hub does have a guard —
`stage_project` refuses to stage a trust whose cohort came back empty or suppressed — but relying
on it would be exactly the "assume the hub filtered first" this layering rejects, and the hub's own
`start_project_imaging_creation` endpoint does not re-check staging.

**Both row-level gates evaluate the cohort as it is now, not as it was at approval.** FLIP has no
frozen approved-cohort artefact: the cohort query is a SQL string that is re-run against live OMOP
at every stage — including by the imaging status poll roughly every 10 seconds while a user has the
project page open. A project can therefore import cleanly and later start refusing if its cohort
shrinks below the threshold. That is the correct behaviour for a disclosure control, but it means
the gate is not a one-time approval-time check; see FLIP#857 for the underlying design gap.

When `/cohort/accession-ids` refuses, imaging-api translates the 403 into a
`CohortBelowThresholdError` rather than a generic transport failure, so the initial pull logs a
clear reason and queues nothing, and the status path reports a readable message to the hub instead
of a raw HTTP error.

### Configuring the threshold

`COHORT_QUERY_THRESHOLD` defaults to `10` and is the trust's own disclosure floor — set it in the
trust's kit file (`trust/.env.<CODE>.<env>`) to raise it. It is not a hub setting and trusts need
not agree on a value.

Note for anyone adding settings here: the service Makefile exports kit-file names with
`sed 's/=.*//'`, which strips the value from *every* line including commented ones, so a
commented-out entry reaches the process as an empty string. Any non-`str` setting therefore needs
an empty-string coercion validator (see `coerce_empty_cohort_query_threshold` in `config.py`), or
the service fails at import.

## Testing

Tests are split into `tests/` (unit-level, no real backing services — `tests/routers/`, `tests/services/`, `tests/db/`, etc.) and `tests/integration/` (real OMOP database via the shared `trust/deploy/compose.test.yml` stack). See [Where does my test go?](../../CONTRIBUTING.md#where-does-my-test-go) in `CONTRIBUTING.md` for the placement rule, and [`trust/README.md`](../README.md#integration-tests-cohort-query-end-to-end) for how the cohort-query end-to-end suite is wired.

```bash
make local_test         # ruff + mypy + unit suite (no Docker required)
make integration_test   # ruff + mypy + /cohort endpoint suite (Docker required)
```

## Further Reading

- [OMOP Database setup](../omop-db/README.md)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
