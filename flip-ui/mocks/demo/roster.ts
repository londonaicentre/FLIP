/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * The public demo's world (FLIP#1746): the two sites every registered project,
 * Connection Status and the FL-nets card agree on, and the viewer identity.
 *
 * The names are the partner institutions' — the same roster the dev stack
 * runs — but the identities (ids, counts) are the demo's own. The values live
 * in roster.json so scripts/demo-fixtures.mjs (which scrubs captures onto this
 * world) reads the same ones.
 */

import roster from "./roster.json";

export interface DemoSite {
    id: string;
    name: string;
    code: string;
    region: string;
}

export const DEMO_SITES: readonly DemoSite[] = roster.sites;

/**
 * The read-only visitor every captured identity is rewritten to. The id is
 * also a sentinel in scripts/assert-no-demo-artefacts.mjs (a Cognito-sub
 * shape that must never appear in a non-demo build).
 */
export const DEMO_USER = roster.user;

export const DEMO_NET_NAME = roster.net;
