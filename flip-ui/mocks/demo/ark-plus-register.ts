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
 * platform record of the Ark+ chest X-ray federated experiments, captured
 * verbatim from the production API and stored as JSON under ./data/.
 *
 * It spans two captures, which is why there are two capture-date constants
 * below:
 *   * the fine-tuning project (project 2b837f54…, model 31569b0b…) was
 *     captured 2026-08-22. It REPLACED an earlier fine-tuning run
 *     (f48850b7…/24985ec3…, captured 2026-07-17) whose every metric was
 *     measured on 90°-rotated radiographs: MONAI's LoadImaged returns a
 *     transposed pixel array and the Ark+ apps' orientation step never undid
 *     it, so Ark+ — pretrained on upright chest X-rays — was scored under a
 *     large distribution shift (FLIP#821, fixed by mt.Transposed). The demo
 *     must not exhibit those numbers as the platform's output.
 *   * the evaluation project (8e404e78…) is still the 2026-07-17 capture, and
 *     its two models are ALSO pre-FLIP#821. Re-capturing it from post-fix runs
 *     is outstanding; see DEMO_EVALUATION_CAPTURE_DATE.
 *
 * KNOWN INCONSISTENCY while the two projects are captured apart: the
 * evaluation project's multimodel run is named "…(finetune 24985ec3)" and its
 * description says it scores "the federated fine-tuned checkpoint produced by
 * the companion training project (model 24985ec3)" — the fine-tuning run that
 * the 2026-08-22 capture replaced. That lineage now dangles: the exhibit shows
 * an evaluation of a training run it no longer contains. Those strings are
 * REAL captured platform metadata and are deliberately left alone — rewriting
 * them to name 31569b0b… would assert a comparison that never ran. The fix is
 * to re-capture the evaluation project from runs that actually scored the new
 * checkpoint, which closes this and the pre-FLIP#821 metrics together.
 *
 * The edits applied to a capture are:
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
 *   5. every trust that took no part in a recorded project — i.e. absent from
 *      every project's `approvedTrusts` — removed from the register entirely:
 *      the cohort query and cohort results, with the derived totals
 *      (`recordCount`, `query.totalCohort`) recomputed from what remains, AND
 *      the estate roster (trusts.json, trust_health.json) that Connection
 *      Status, its topology view and the FL nets card all read.
 *
 *      This is not cosmetic. flip-api broadcasts every cohort query to EVERY
 *      registered trust (`select(Trust)` with no filter, see
 *      cohort_services/submit_cohort_query.py) — answering one is a discovery
 *      step that happens before trust approval and implies no involvement in
 *      the project. The evaluation capture therefore carried AI Centre Private,
 *      an internal node approved for neither demo project: it was registered on
 *      the hub between the two captures, which is the only reason the original
 *      fine-tuning project (queried 6 Jul) had two trusts and the evaluation
 *      project (queried 17 Jul) had three. Publishing that put a private node's
 *      per-finding record counts on an unauthenticated page, and made a project
 *      named "(2-trust)" with two approved trusts advertise a cohort of 1,414
 *      that only added up by counting the non-participant. It is now 946
 *      (FLIP#794 review).
 *
 *      The 2026-08-22 fine-tuning capture shows how routine this is: it was
 *      broadcast to FOUR registered trusts and approved for two. Guy's and St
 *      Thomas' answered with a below-threshold cohort (so it arrived already
 *      `trustSuppressed`, carrying a record count of 0) and AI Centre Private
 *      was cancelled. Both are stripped from `queriedTrustIds`,
 *      `respondedTrustIds`, `emptyTrustIds`, `cancelledTrustIds`,
 *      `trustRecordCounts` and `trustSuppressed`. Here the totals happened to
 *      need no recomputation — neither non-participant contributed a row, so
 *      `recordCount` and `query.totalCohort` were already the two-trust sum
 *      (1,910 + 1,872 = 3,782) — but CHECK rather than assume: that is a
 *      property of this capture, not a rule.
 *
 *      The roster goes with it. Keeping an estate view "truthful" is a
 *      semantic borrowed from the live app, and this is a curated snapshot of
 *      two projects rather than a live estate: leaving the node listed there
 *      published exactly the identity the cohort edit removed — name, code,
 *      region, id — and left its PROJECTS column reading 1 against a project
 *      that appears nowhere in the exhibit. Two trusts everywhere is both the
 *      smaller disclosure and the consistent story.
 *   6. every count recomputed so it equals what the register itself contains,
 *      whichever way the capture got it wrong:
 *
 *        * `project_count` (trusts.json) was the live production figure — KCL
 *          41, BDMS 34, each trust's real project load — published on an
 *          unauthenticated page and contradicting an exhibit that holds two
 *          projects. It counts `project_trust_intersect` rows (flip-api
 *          trusts_services/get_trusts.py), so against this register it is the
 *          number of recorded projects whose `approvedTrusts` name the trust:
 *          2 for both. Rendered three times on Connection Status — the
 *          PROJECTS column, the trust card, and the topology node label, whose
 *          radius it also scales.
 *        * `userCount` (project.json, p2_project.json) was 0 on projects that
 *          each list one user, because the capture came from the project
 *          DETAIL endpoint, which never populates it — only the list endpoint
 *          does (project_services/get_projects.py `_load_user_counts`, which
 *          counts ProjectUserAccess rows and includes the owner). demo-server
 *          serves those detail payloads on /projects, where the UI reads the
 *          list shape, so the projects page rendered "0 users" for a project
 *          that has an owner. It is 1 for both.
 *
 *   7. every entity named consistently across the register. P1's cohort
 *      results called trust 16f5b300… "Guy's and St Thomas' Trust" while every
 *      other payload — including the same project's approvedTrusts — called it
 *      "King's College London". Not a capture error: `query_result` stores only
 *      `trust_id`, and private_services/receive_cohort_results.py joins
 *      `Trust.name` at AGGREGATION time and freezes it into the QueryStats
 *      blob. So a cohort aggregate carries the trust's name as of the day it
 *      was aggregated (6 Jul for P1) while everything else joins the live trust
 *      row (17 Jul), and the trust was renamed in between. The register is
 *      presented as a single snapshot dated DEMO_CAPTURE_DATE, so the later
 *      name wins. Latent rather than visible today — AiCohortChart labels from
 *      the roster (`trust?.code || trustName`) and only falls back to the
 *      payload name for a trust the roster does not have, which rule 5 already
 *      forbids — but two names for one id is a contradiction the register
 *      should not carry.
 *
 *   8. the fine-tuning project's and model's TITLES AND DESCRIPTIONS carried
 *      over from the run the 2026-08-22 capture replaced, by decision of the
 *      project owner. This is the one edit that adds prose rather than removing
 *      it, so it is called out separately from the seven rules above: the real
 *      prod records carry an empty description and the model name
 *      "finetunning", which reads as a broken exhibit on a public page.
 *
 *      It is sound because the re-run is the SAME experiment — the earlier
 *      copy describes this run correctly, and every specific claim in it was
 *      checked against the new capture rather than assumed: Swin-Large at
 *      768px (ARKPLUS.MODEL_NAME/INPUT_SIZE), frozen encoder with a fresh
 *      5-class head (LOAD_BACKBONE_ONLY, AGGREGATE_ONLY_REGEX omni_heads,
 *      NUM_CLASSES_LIST [5]), 50 global rounds of 5 local epochs
 *      (GLOBAL_ROUNDS/LOCAL_ROUNDS), batch size 4 (BATCH_SIZE), LR 1.5e-3 to
 *      1.5e-4 (LR_START/LR_END), 20% held out per site (VAL_SPLIT), and
 *      "per-round payloads in the tens of kilobytes" — which the captured
 *      logs put at 26.9 KB a round.
 *
 *      One claim did NOT survive that check and was dropped: the old model
 *      name pinned the app build "(arkplus-apps 8a188867)", which is the
 *      PRE-FLIP#821 build. This run used the fixed apps, so carrying it would
 *      have named the wrong code. The name is now "Ark+ Finetuning 50r
 *      client_api".
 *
 *      If the prod records are ever given real titles and descriptions, drop
 *      this rule and capture them instead — a value read from the platform
 *      always beats one restored by hand.
 *
 * Institution names are shown verbatim by decision of the project owner: the
 * two participating trusts (King's College London, Bangkok Dusit Medical
 * Services), and Guy's and St Thomas' NHS Foundation Trust, which after rule 7
 * appears only as a copyright holder in the licence header of the cohort SQL —
 * rendered on the cohort page — rather than as the name of any trust.
 *
 * Re-capturing the register means re-applying rules 1-7, and deciding afresh
 * whether rule 8 is still needed. Project and model
 * ids are deliberately NOT scrubbed: they are public URL path segments.
 */

/** The real Ark+ experiment record (public URL path segments). */
export const DEMO_PROJECT_ID = "2b837f54-8052-4bfa-b75b-c05f036e6086";
export const DEMO_MODEL_ID = "31569b0b-7698-4bf4-a446-59643c6674e0";

/**
 * The date the fine-tuning project was captured from the production API (see
 * the provenance note above) — and the newest capture in the register, so it
 * identifies the snapshot as a whole. Consumed by:
 *   1. src/demo/DemoBanner.vue, rendered on every page of the demo build; and
 *   2. scripts/generate-demo-window-js.sh, which greps this file for the
 *      value so window.RELEASE_VERSION can't drift from it independently.
 * Update this constant (and the prose above) together when the register is
 * re-captured.
 */
export const DEMO_CAPTURE_DATE = "2026-08-22";

/**
 * The evaluation project's capture date, which is NOT the same as
 * DEMO_CAPTURE_DATE: the fine-tuning project was re-captured on its own when
 * the Ark+ apps' image-orientation bug was fixed (FLIP#821 — every metric in
 * the original capture was measured on 90°-rotated radiographs), and the
 * evaluation project was left on its earlier capture.
 *
 * The register consequently spans two dates and the banner names both. Do not
 * collapse this back to one constant while the two projects are captured
 * apart: a single date would misdate half the exhibit, and the register's whole
 * provenance discipline rests on the labelling being exactly true.
 *
 * The evaluation project's numbers are ALSO pre-FLIP#821 — its two models were
 * scored on the same rotated images — so re-capturing it from post-fix runs is
 * outstanding work, after which both dates converge again and this constant
 * should go.
 */
export const DEMO_EVALUATION_CAPTURE_DATE = "2026-07-17";

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
