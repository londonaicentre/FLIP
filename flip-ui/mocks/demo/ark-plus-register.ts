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
 * API on 2026-07-17 and stored as JSON under ./data/. The only edits applied
 * at capture time were:
 *   1. every e-mail address replaced with demo@flip.local;
 *   2. every personal name (e.g. the query creator's) replaced with the
 *      demo identity "FLIP Demo" — the register is published
 *      unauthenticated, so it must carry no individual's identifiers; and
 *   3. the live presigned S3 URL in fl_results.json replaced with an inert
 *      relative path (a leaked presigned URL is a time-limited download
 *      capability; the demo must ship none).
 *
 * Institution names (King's College London, Bangkok Dusit Medical Services,
 * Guy's and St Thomas' Trust, AI Centre Private) are shown verbatim by
 * decision of the project owner.
 */

/** The real Ark+ experiment record (public URL path segments). */
export const DEMO_PROJECT_ID = "f48850b7-3101-46d1-8fa7-a00bf01d2597";
export const DEMO_MODEL_ID = "24985ec3-3349-435b-afcd-f38972d8695d";

/**
 * Read-only viewer identity seeded by src/demo/bootstrap.ts and served from
 * GET /users/:email. Empty permissions => `isViewer` everywhere, so every
 * mutating control in the UI hides itself.
 */
export const DEMO_USER = {
    id: "c622c224-d0d1-704e-0b6d-711d8d4605ca",
    email: "demo@flip.local",
    name: "FLIP Demo",
    organisation: "FLIP",
    isDisabled: false
};
