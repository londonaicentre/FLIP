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
 * Ark+ chest X-ray evaluation: a single-model baseline and a pretrained-vs-
 * fine-tuned comparison scored on each site's hold-out set. Captured before
 * the FLIP#821 orientation fix, so its scores predate it; the comparison
 * model's name points at an earlier fine-tuning run than the one shown
 * here.
 *
 * The data here is already on the demo world (scripts/demo-fixtures.mjs scrub,
 * driven by ./capture.json); this module only wires it up.
 */

import type { DemoProject } from "../../registry";
import cohort from "./data/cohort.json";
import imageStatus from "./data/image_status.json";
import m1Config from "./data/m1_config.json";
import m1Files from "./data/m1_files.json";
import m1Logs from "./data/m1_logs.json";
import m1Metrics from "./data/m1_metrics.json";
import m1Step from "./data/m1_step.json";
import m2Config from "./data/m2_config.json";
import m2Files from "./data/m2_files.json";
import m2Logs from "./data/m2_logs.json";
import m2Metrics from "./data/m2_metrics.json";
import m2Step from "./data/m2_step.json";
import models from "./data/models.json";
import project from "./data/project.json";

export const ARKPLUS_EVALUATION: DemoProject = {
    slug: "arkplus-evaluation",
    captureDate: "2026-07-17",
    project,
    models,
    cohort,
    // Captured before per-trust cohort snapshots existed (FLIP#857): none to show.
    cohortSnapshots: [],
    imageStatus,
    modelFixtures: {
        [m1Step.modelId]: {
            step: m1Step,
            config: m1Config,
            metrics: m1Metrics,
            logs: m1Logs,
            files: m1Files
        },
        [m2Step.modelId]: {
            step: m2Step,
            config: m2Config,
            metrics: m2Metrics,
            logs: m2Logs,
            files: m2Files
        }
    }
};
