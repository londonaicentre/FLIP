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


import { createTestingPinia } from "@pinia/testing";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import Training from "@/partials/models/Training.vue";
import { ModelStatus } from "@/services/model-service";

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({
            params: { modelId: "model-1" },
            query: {}
        })
    };
});

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        initialiseTraining: vi.fn()
    };
});

const alertStub = { template: "<div data-test=\"alert-stub\"><slot /></div>" };
const buttonStub = { template: "<button data-test=\"initiate-training-btn\"><slot /></button>" };
const actionsMenuStub = { template: "<div data-test=\"training-actions-menu\" />" };

interface MountOpts {
    permissions?: string[];
    status?: ModelStatus;
    allFilesUploaded?: boolean;
    requiredFiles?: string[];
    uploadedFileNames?: string[];
    jobType?: string;
    flBackendLabel?: string;
}

function mountTraining(options: MountOpts = {}) {
    const {
        permissions = ["CanCreateProjects"],
        status = "PENDING",
        allFilesUploaded = false,
        requiredFiles = ["trainer.py", "config.json"],
        uploadedFileNames = [],
        jobType = "standard",
        flBackendLabel
    } = options;

    return mount(Training, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "testuser",
                                userId: "1",
                                attributes: {
                                    sub: "1",
                                    email: "t@e.co"
                                },
                                permissions
                            },
                            signInStep: "DONE"
                        }
                    }
                })
            ],
            renderStubDefaultSlot: true,
            stubs: {
                AiCard: { template: "<div><slot /></div>" },
                AiAlert: alertStub,
                AiButton: buttonStub,
                TrainingActionsMenu: actionsMenuStub,
                TrainingOptions: { template: "<div data-test=\"training-options\" />" },
                TrainingMetrics: { template: "<div />" },
                Timeline: { template: "<div />" },
                Form: { template: "<form><slot :errors=\"{}\" /></form>" }
            }
        },
        props: {
            canTrain: true,
            status,
            allFilesUploaded,
            requiredFiles,
            uploadedFileNames,
            jobType,
            flBackendLabel
        }
    });
}

describe("Training viewer-aware rendering", () => {
    // The Initiate Training button lives on the model page wrapper
    // (pages/project/[projectId]/model/[modelId]/index.vue), not on the
    // Training partial. The viewer-hide rule there is covered by the
    // page's own spec. Only the actions-menu visibility is asserted here.
    it("hides the actions menu for viewers", () => {
        const wrapper = mountTraining({ permissions: [] });

        expect(wrapper.find("[data-test=training-actions-menu]").exists()).toBe(false);
    });
});

describe("Training missing-files alert slot", () => {
    it("lists required files and the missing subset when files are missing", () => {
        const wrapper = mountTraining({
            allFilesUploaded: false,
            requiredFiles: ["trainer.py", "config.json"],
            uploadedFileNames: ["trainer.py"],
            jobType: "diffusion",
            flBackendLabel: "Flower"
        });

        // Two AiAlerts render side-by-side: an info-variant listing the
        // required files and a warning-variant listing the missing subset.
        // Both stubs share the data-test, so concat their HTML and assert
        // against the combined content.
        const html = wrapper.findAll("[data-test=alert-stub]").map(a => a.html()).join("\n");

        expect(html).toContain("Flower");
        expect(html).toContain("diffusion");
        expect(html).toContain("trainer.py");
        expect(html).toContain("config.json");
        expect(html).toContain("Missing:");
    });

    it("falls back to the generic message when allFilesUploaded is false but no specific file is missing", () => {
        const wrapper = mountTraining({
            allFilesUploaded: false,
            requiredFiles: [],
            uploadedFileNames: []
        });

        const slot = wrapper.find("[data-test=alert-stub]");

        expect(slot.exists()).toBe(true);
        expect(slot.html()).toContain("All required model files must be uploaded");
        expect(slot.html()).not.toContain("Missing:");
    });

    it("hides the alert when allFilesUploaded is true", () => {
        const wrapper = mountTraining({ allFilesUploaded: true });

        expect(wrapper.find("[data-test=alert-stub]").exists()).toBe(false);
    });

    it("escapes job type and file names so user-controlled values cannot inject HTML", () => {
        const payload = "<img src=x onerror=alert(1)>";

        const wrapper = mountTraining({
            allFilesUploaded: false,
            requiredFiles: [payload],
            uploadedFileNames: [],
            jobType: payload
        });

        const slot = wrapper.find("[data-test=alert-stub]");

        expect(slot.find("img").exists()).toBe(false);
        expect(slot.html()).toContain("&lt;img");
    });
});

describe("Training metrics + live-activity responsive layout", () => {
    // The metrics chart and the Live activity timeline sit side-by-side on wide
    // screens but must stack — activity below the chart, full-width — on narrow
    // ones, mirroring how the Model files card reflows. Otherwise the fixed
    // 384px activity card overflows a phone viewport and squashes the chart.
    function layoutParts(wrapper: ReturnType<typeof mountTraining>) {
        const timeline = wrapper.find("[data-test=training-timeline]");
        expect(timeline.exists()).toBe(true);
        // Timeline -> scroll wrapper -> inner column -> Live activity card ->
        // flex container; the metrics card is that container's first child.
        const scrollWrap = timeline.element.parentElement;
        const innerColumn = scrollWrap?.parentElement;
        const liveCard = innerColumn?.parentElement;
        const container = liveCard?.parentElement;
        const metricsCard = container?.children[0];

        return {
            scrollWrap,
            innerColumn,
            liveCard,
            container,
            metricsCard
        };
    }

    it("stacks the cards by default and only goes side-by-side on wide (xl) screens", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { container } = layoutParts(wrapper);

        expect(container?.className).toContain("flex-col");
        expect(container?.className).toContain("xl:flex-row");
    });

    it("makes the live-activity card full-width when stacked and a fixed column only at xl+", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { liveCard } = layoutParts(wrapper);

        expect(liveCard?.className).toContain("w-full");        // full-width when stacked (mobile)
        expect(liveCard?.className).toContain("xl:w-96");       // fixed sidebar width when side-by-side
        expect(liveCard?.className).toContain("xl:shrink-0");   // only pins its width in row mode
    });

    it("lets the metrics card take the remaining width beside the activity card", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { metricsCard } = layoutParts(wrapper);

        expect(metricsCard?.className).toContain("flex-1");
        expect(metricsCard?.className).toContain("min-w-0");
    });

    it("matches the sibling card heights at xl instead of letting the timeline set them", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { liveCard, innerColumn } = layoutParts(wrapper);

        // The card stretches to the row height its siblings define — no 70vh cap.
        expect(liveCard?.className).toContain("self-stretch");
        expect(liveCard?.className).not.toContain("max-h-[70vh]");
        // At xl the content column is absolutely positioned, so the timeline's
        // length cannot drive the card's intrinsic height past its siblings.
        expect(innerColumn?.className).toContain("xl:absolute");
        expect(innerColumn?.className).toContain("xl:inset-0");
    });

    it("never lets the timeline scroll horizontally", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { scrollWrap } = layoutParts(wrapper);

        expect(scrollWrap?.className).toContain("overflow-x-hidden");
    });
});

describe("Training Live activity status dot", () => {
    const dotSelector = "[data-test=live-activity-dot]";

    it("is primary (in-progress) when training is running", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-primary-600");
    });

    it("is red when the model is in ERROR", () => {
        const wrapper = mountTraining({ status: "ERROR" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-red-600");
        expect(dot.classes()).not.toContain("bg-primary-600");
        expect(dot.classes()).not.toContain("bg-gray-400");
    });

    it("is gray when the model is STOPPED", () => {
        const wrapper = mountTraining({ status: "STOPPED" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-gray-400");
    });

    it("is gray when results have been uploaded", () => {
        const wrapper = mountTraining({ status: "RESULTS_UPLOADED" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-gray-400");
    });

    it("does not animate the ping ring on ERROR", () => {
        const wrapper = mountTraining({ status: "ERROR" });

        expect(wrapper.find("[data-test=live-activity-ping]").exists()).toBe(false);
    });
});
