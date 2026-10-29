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

# The trust observability dashboard (FLIP#1584)

[`flip_trust_observability.sql`](flip_trust_observability.sql) is the dashboard, kept as the SQL
of its tiles. Snowsight dashboards have no REST API and no Terraform resource, so there is nothing
for the platform root to apply: the views the tiles read are Terraform-managed
(`../observability_views.tf`), the arrangement of tiles is not.

Every tile reads a view in `FLIP_PLATFORM.OBS`. No tile touches the event table or
`SNOWFLAKE.ACCOUNT_USAGE` directly, which is why `FLIP_<CODE>_OBSERVER` needs `SELECT` on the
views and nothing else: the views are owner's-rights and the platform tier owns them.

## Who can see it

`FLIP_<CODE>_OBSERVER`, granted by the platform root to `FLIP_<CODE>_DEPLOY` only. A person gets
it the same way the break-glass operator gets `ACCOUNTADMIN`: an admin grants the role, in
Snowsight, to the named user. `FLIP_<CODE>_SVC` has no `USAGE` on `FLIP_PLATFORM` at all, so a
running container cannot read its neighbours' logs.

## As a worksheet (one minute)

1. Snowsight, Projects, Worksheets, plus, SQL Worksheet.
2. Paste the file in, replace every `<CODE>` with the trust code, run the three `USE` statements,
   then run whichever tile you want.

That is the supported path for a one-off look at a trust, and it is the whole file verbatim.

## As a dashboard (five minutes, once per account)

Snowsight builds dashboards tile by tile; there is no import.

1. Snowsight, Projects, Dashboards, plus, name it `FLIP trust <CODE> observability`.
2. For each `-- TILE:` block in the file: New Tile, From SQL Worksheet, paste the statement,
   set the role to `FLIP_<CODE>_OBSERVER` and the warehouse to `FLIP_<CODE>_WH`, run it, and name
   the tile after the marker. Where the marker says `(bar chart)` or `(scorecard)`, switch the
   tile to Chart and pick that type; the rest stay as tables.
3. Share the dashboard with `FLIP_<CODE>_OBSERVER`.

Tiles are in the order they should be laid out: status and restarts first, errors next, fl-client
jobs, then cost.

## Keeping it honest

The tiles are plain SQL against named views, so a view rename breaks a tile loudly rather than
silently returning nothing. The static guards in `../tests/test_static_guards.py` pin both ends:
every view name Terraform creates must appear in this file, and every `FLIP_PLATFORM.OBS.V_*`
name this file reads must be a view Terraform creates.

## Not in scope here

Grafana on SPCS is the follow-up (see "Open points" in `../README.md`). Nothing in this directory
assumes it: these tiles stay useful whether or not a Grafana service is ever deployed, and they
are the only observability a trial account (no external access integration, so no alerting out)
can have.
