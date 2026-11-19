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
 * The public demo's project register (FLIP#1746): every project the exhibit
 * serves, in display order. Each project's data is already on the demo world on
 * disk (scripts/demo-fixtures.mjs scrub); mocks/demo-server.ts serves it as is.
 *
 * Only demo-server.ts (loaded dynamically behind the VITE_DEMO literal) and the
 * tests import this module: it carries every fixture JSON, so anything in the
 * main bundle that needs the exhibit's shape reads capture-window.ts instead.
 */

import { ARKPLUS_EVALUATION } from "./projects/arkplus-evaluation/register";
import { ARKPLUS_FINETUNE } from "./projects/arkplus-finetune/register";
import { EHR_RISK } from "./projects/ehr-risk/register";
import { SPLEEN_EVALUATION } from "./projects/spleen-evaluation/register";
import { SPLEEN_SEGMENTATION } from "./projects/spleen-segmentation/register";

/** Results are never captured: the demo is browse-only, so /fl/results answers []. */
export interface DemoModelFixtures {
    step: unknown;
    config: unknown;
    metrics: unknown[];
    logs: unknown;
    files: unknown[];
}

export interface DemoProject {
    slug: string;
    /** ISO date the fixtures were captured. */
    captureDate: string;
    project: unknown;
    models: unknown;
    cohort: unknown;
    /** Per-trust frozen cohort membership (FLIP#857); empty for a capture that predates it. */
    cohortSnapshots: unknown[];
    /** A tabular-only project (has_imaging=false) may carry none; the UI never asks for it. */
    imageStatus?: unknown;
    /** Keyed by model id. */
    modelFixtures: Record<string, DemoModelFixtures>;
}

export const DEMO_PROJECTS: readonly DemoProject[] = [
    ARKPLUS_FINETUNE,
    ARKPLUS_EVALUATION,
    EHR_RISK,
    SPLEEN_SEGMENTATION,
    SPLEEN_EVALUATION
];

/** What capture-window.ts must say; its spec pins the two together. */
export function captureWindow(): { projectCount: number; earliest: string; latest: string } {
    const dates = DEMO_PROJECTS.map(p => p.captureDate).sort();

    return {
        projectCount: DEMO_PROJECTS.length,
        earliest: dates[0] as string,
        latest: dates[dates.length - 1] as string
    };
}
