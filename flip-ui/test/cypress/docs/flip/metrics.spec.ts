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

// Records docs/source/assets/flip/metrics.gif.
//
// TrainingMetrics is mounted in partials/models/Training.vue under
// `v-if="getStatus !== ModelStatusEnum.PENDING"`, so the model fixture must
// land at a post-PENDING status. We patch it to TRAINING_STARTED so the
// AiSteps panel reads as a mid-training run (Model Created / Model Prepared
// completed, Training Started in progress, Results Uploaded not yet
// reached), matching the streaming-metrics framing of the GIF. The bundled
// fixture's "UPLOAD_COMPLETED" placeholder is not a member of
// ModelStatusEnum, so getStatusEnumValue would otherwise fall through to
// ModelStatusEnum.ERROR and paint both "Training Started" and "Results
// Uploaded" with the red-cross icon.
//
// The widget fetches GET /model/{id}/metrics; no bundled fixture covers it,
// so the spec inlines a two-chart IModelMetricData[] payload that
// AiMetricsChart renders into <canvas> elements.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

const METRICS = [
    {
        yLabel: "Loss",
        xLabel: "Round",
        metrics: [
            {
                seriesLabel: "Global",
                data: [
                    { xValue: 1, yValue: 1.42 },
                    { xValue: 2, yValue: 1.18 },
                    { xValue: 3, yValue: 0.96 },
                    { xValue: 4, yValue: 0.81 },
                    { xValue: 5, yValue: 0.72 },
                    { xValue: 6, yValue: 0.65 },
                    { xValue: 7, yValue: 0.60 },
                    { xValue: 8, yValue: 0.57 }
                ]
            }
        ]
    },
    {
        yLabel: "Dice score",
        xLabel: "Round",
        metrics: [
            {
                seriesLabel: "Global",
                data: [
                    { xValue: 1, yValue: 0.42 },
                    { xValue: 2, yValue: 0.55 },
                    { xValue: 3, yValue: 0.63 },
                    { xValue: 4, yValue: 0.71 },
                    { xValue: 5, yValue: 0.77 },
                    { xValue: 6, yValue: 0.81 },
                    { xValue: 7, yValue: 0.84 },
                    { xValue: 8, yValue: 0.86 }
                ]
            }
        ]
    }
];

describe("docs: training metrics", () => {
    it("shows the per-round metrics charts for a finished model", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.fixture("model/getModelPostTraining").then((model) => {
            model.status = "TRAINING_STARTED";
            cy.intercept("POST", `/step/model/${MODEL_ID}`, { body: model }).as("getModel");
        });
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("GET", "/model/job-types", { body: { standard: ["trainer.py", "validator.py", "models.py", "config.json"] } });
        cy.intercept("GET", `/model/${MODEL_ID}/metrics`, { body: METRICS }).as("getMetrics");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.wait("@getMetrics");
        cy.demoPause();

        cy.get("canvas").first().scrollIntoView();
        cy.demoPause(2400);
    });
});
