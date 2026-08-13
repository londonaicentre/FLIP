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
 * Identity constants for the public Ark+ demo (VITE_DEMO build).
 *
 * The register itself — every API payload the demo serves — is the *real*
 * platform record of the Ark+ chest X-ray federated fine-tuning experiment
 * (project f48850b7…, model 24985ec3…), captured verbatim from the production
 * API on 2026-07-17 and stored as JSON under ./data/. The edits applied to the
 * capture were:
 *   1. every e-mail address replaced with demo@flip.local;
 *   2. every personal name (e.g. the query creator's) replaced with the
 *      demo identity "FLIP Demo" — the register is published
 *      unauthenticated, so it must carry no individual's identifiers;
 *   3. the live presigned S3 URL in fl_results.json replaced with the
 *      CloudFront-served demo-assets URL (/ark_demo/assets/*, OAC-locked
 *      bucket behind the production distribution — a leaked presigned URL
 *      is a time-limited download capability; the demo must ship none); and
 *   4. every user id replaced with a synthetic
 *      00000000-0000-4000-8000-0000000000NN value. A FLIP user id IS a Cognito
 *      `sub` (see flip-api user_models.py) and the pool/client ids are already
 *      public, so shipping the real ones published the actual principals used
 *      for permissions and ownership lookups — rule 2 above was written to
 *      cover exactly that and the original capture did not honour it
 *      (FLIP#794 review). These ids are only opaque join keys inside the mock,
 *      so any distinct values work; keep them obviously synthetic. The demo
 *      viewer is …0001, the evaluation project's owner …0002.
 *
 * Institution names (King's College London, Bangkok Dusit Medical Services,
 * Guy's and St Thomas' Trust, AI Centre Private) are shown verbatim by
 * decision of the project owner.
 *
 * Re-capturing the register means re-applying all four rules. Project and model
 * ids are deliberately NOT scrubbed: they are public URL path segments.
 */

/** The real Ark+ experiment record (public URL path segments). */
export const DEMO_PROJECT_ID = "f48850b7-3101-46d1-8fa7-a00bf01d2597";
export const DEMO_MODEL_ID = "24985ec3-3349-435b-afcd-f38972d8695d";

/**
 * The date the register was captured from the production API (see the
 * provenance note above) — the single source of truth for the demo's
 * "snapshot of …, captured …" labelling. Consumed by:
 *   1. src/demo/DemoBanner.vue, rendered on every page of the demo build; and
 *   2. scripts/generate-demo-window-js.sh, which greps this file for the
 *      value so window.RELEASE_VERSION can't drift from it independently.
 * Update this constant (and the prose above) together when the register is
 * re-captured.
 */
export const DEMO_CAPTURE_DATE = "2026-07-17";

/**
 * Read-only viewer identity seeded by src/demo/bootstrap.ts and served from
 * GET /users/:email. Empty permissions => `isViewer` everywhere, so every
 * mutating control in the UI hides itself.
 */
export const DEMO_USER = {
    id: "00000000-0000-4000-8000-000000000001",
    email: "demo@flip.local",
    name: "FLIP Demo",
    organisation: "FLIP",
    isDisabled: false
};
