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
 * Spleen segmentation evaluation: scores the tutorial's shipped spleen
 * model on each site's CT without retraining.
 *
 * The data here is already on the demo world (scripts/demo-fixtures.mjs scrub,
 * driven by ./capture.json); this module only wires it up.
 */

import type { DemoProject } from "../../registry";
import cohort from "./data/cohort.json";
import cohortSnapshots from "./data/cohort_snapshots.json";
import imageStatus from "./data/image_status.json";
import m1Config from "./data/m1_config.json";
import m1Files from "./data/m1_files.json";
import m1Logs from "./data/m1_logs.json";
import m1Metrics from "./data/m1_metrics.json";
import m1Step from "./data/m1_step.json";
import models from "./data/models.json";
import project from "./data/project.json";

export const SPLEEN_EVALUATION: DemoProject = {
    slug: "spleen-evaluation",
    captureDate: "2026-11-02",
    project,
    models,
    cohort,
    cohortSnapshots,
    imageStatus,
    modelFixtures: {
        [m1Step.modelId]: {
            step: m1Step,
            config: m1Config,
            metrics: m1Metrics,
            logs: m1Logs,
            files: m1Files
        }
    }
};
