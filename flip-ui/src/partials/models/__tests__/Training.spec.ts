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
import { flushPromises, mount } from "@vue/test-utils";
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

const alertStub = {
    props: ["variant", "actionText"],
    emits: ["action"],
    template: "<div data-test=\"alert-stub\" :data-variant=\"variant\"><slot />"
        + "<button v-if=\"actionText\" data-test=\"alert-action\" @click=\"$emit('action')\">"
        + "{{ actionText }}</button></div>"
};
const buttonStub = { template: "<button data-test=\"initiate-training-btn\"><slot /></button>" };
const actionsMenuStub = { template: "<div data-test=\"training-actions-menu\" />" };

interface MountOpts {
    permissions?: string[];
    status?: ModelStatus;
    view?: "prepare" | "run";
    allFilesUploaded?: boolean;
    requiredFiles?: string[];
    uploadedFileNames?: string[];
    jobType?: string;
    flBackendLabel?: string;
    jobTypesError?: boolean;
    jobTypesLoading?: boolean;
    runTrusts?: string[];
    formValues?: Record<string, unknown>;
}

// Stands in for the vee-validate form's reactive `values`, which Training reads
// off the Form ref to know whether the run options are complete.
let currentFormValues: Record<string, unknown> = {};

function mountTraining(options: MountOpts = {}) {
    const {
        permissions = ["CanCreateProjects"],
        status = "PENDING",
        allFilesUploaded = false,
        requiredFiles = ["trainer.py", "config.json"],
        uploadedFileNames = [],
        jobType = "standard",
        flBackendLabel,
        jobTypesError = false,
        jobTypesLoading = false,
        // Mirrors the page default: nothing to watch until the model is dispatched.
        view = status === "PENDING" ? "prepare" : "run",
        runTrusts = [],
        formValues = {}
    } = options;

    currentFormValues = formValues;

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
                TrainingOptions: {
                    props: ["disabled"],
                    template: "<div data-test=\"training-options\" :data-disabled=\"disabled\" />"
                },
                TrainingMetrics: { template: "<div />" },
                Timeline: { template: "<div />" },
                Form: {
                    props: ["initialValues"],
                    setup: () => ({ values: currentFormValues }),
                    template: "<form :data-initial-values=\"JSON.stringify(initialValues ?? null)\">" +
                        "<slot :errors=\"{}\" /></form>"
                }
            }
        },
        props: {
            canTrain: true,
            status,
            allFilesUploaded,
            requiredFiles,
            uploadedFileNames,
            jobType,
            flBackendLabel,
            jobTypesError,
            jobTypesLoading,
            view,
            runTrusts
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
        // Timeline -> scroll wrapper -> Live activity card -> flex container;
        // the metrics card is that container's first child.
        const scrollWrap = timeline.element.parentElement;
        const liveCard = scrollWrap?.parentElement;
        const container = liveCard?.parentElement;
        const metricsCard = container?.children[0];

        return {
            scrollWrap,
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

    it("lets both cards stretch to the window instead of pinning them to a fixed height", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { container, metricsCard, liveCard } = layoutParts(wrapper);

        // The row is bounded by the page, so releasing the cards' heights at xl
        // makes them fill it rather than run past it.
        expect(container?.className).toContain("flex-1");
        expect(container?.className).toContain("min-h-0");
        expect(metricsCard?.className).toContain("xl:min-h-0");
        expect(liveCard?.className).toContain("xl:min-h-0");
    });

    it("splits the height between the stacked cards, with floors for a short phone", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { metricsCard, liveCard } = layoutParts(wrapper);

        // Stacked, both grow; at xl the activity card stops growing and pins its width.
        expect(metricsCard?.className).toContain("flex-1");
        expect(liveCard?.className).toContain("flex-1");
        expect(liveCard?.className).toContain("xl:flex-none");
        expect(metricsCard?.className).toContain("min-h-[24rem]");
        expect(liveCard?.className).toContain("min-h-[20rem]");
    });

    it("scrolls the activity feed inside its card rather than running off the page", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { scrollWrap } = layoutParts(wrapper);

        expect(scrollWrap?.className).toContain("overflow-y-auto");
        expect(scrollWrap?.className).toContain("min-h-0");
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

    it("keeps a long timeline from driving the card's height", () => {
        const wrapper = mountTraining({ status: "RUNNING" });

        const { liveCard } = layoutParts(wrapper);

        // The card's height comes from the row, and the feed scrolls inside it.
        expect(liveCard?.className).not.toContain("max-h-[70vh]");
        expect(liveCard?.className).toContain("min-h-0");
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

    it("is green when the results have been uploaded", () => {
        // A run that finished and delivered its results is a success, not a
        // greyed-out non-event.
        const wrapper = mountTraining({ status: "RESULTS_UPLOADED" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-emerald-500");
    });

    it("is gray when the model is STOPPED", () => {
        const wrapper = mountTraining({ status: "STOPPED" });

        const dot = wrapper.find(dotSelector);

        expect(dot.exists()).toBe(true);
        expect(dot.classes()).toContain("bg-gray-400");
    });

    it("does not animate the ping ring on ERROR", () => {
        const wrapper = mountTraining({ status: "ERROR" });

        expect(wrapper.find("[data-test=live-activity-ping]").exists()).toBe(false);
    });
});

describe("Training view", () => {
    it("the prepare view shows the run options and no monitoring", () => {
        const wrapper = mountTraining({
            view: "prepare",
            status: "PENDING"
        });

        expect(wrapper.find("[data-test=training-options]").exists()).toBe(true);
        expect(wrapper.find("[data-test=training-timeline]").exists()).toBe(false);
    });

    it("the prepare view keeps the run options on screen once dispatched, but locked", () => {
        // The configuration a run was launched with is worth reading back; it just
        // must not be editable.
        const wrapper = mountTraining({
            view: "prepare",
            status: "RUNNING"
        });

        const options = wrapper.find("[data-test=training-options]");
        expect(options.exists()).toBe(true);
        expect(options.attributes("data-disabled")).toBe("true");
        expect(wrapper.find("[data-test=training-timeline]").exists()).toBe(false);
    });

    it("the prepare view leaves the run options editable while pending", () => {
        const wrapper = mountTraining({
            view: "prepare",
            status: "PENDING"
        });

        expect(wrapper.find("[data-test=training-options]").attributes("data-disabled")).toBe("false");
    });

    it("the run view shows monitoring and never the options form", () => {
        const wrapper = mountTraining({
            view: "run",
            status: "RUNNING"
        });

        expect(wrapper.find("[data-test=training-timeline]").exists()).toBe(true);
        expect(wrapper.find("[data-test=training-options]").exists()).toBe(false);
        expect(wrapper.find("form").exists()).toBe(false);
    });
});


describe("Training options reflect a dispatched run", () => {
    const initialValues = (wrapper: ReturnType<typeof mountTraining>) =>
        JSON.parse(wrapper.get("form").attributes("data-initial-values") ?? "null");

    it("leaves a pending model's form empty — nothing has been decided yet", () => {
        const wrapper = mountTraining({
            status: "PENDING",
            view: "prepare"
        });

        expect(initialValues(wrapper)).toBeNull();
    });

    it("pre-fills the trusts a dispatched run went to, so the switches read as on", () => {
        const wrapper = mountTraining({
            status: "RUNNING",
            view: "prepare",
            runTrusts: ["trust-a", "trust-b"]
        });

        // Enrichment is a gate on dispatch: a run that started was confirmed enriched.
        expect(initialValues(wrapper)).toEqual({
            enriched: "true",
            trust_ids: ["trust-a", "trust-b"]
        });
    });
});


describe("Training reports whether the run options are complete", () => {
    const optionsComplete = (formValues: Record<string, unknown>) => {
        const wrapper = mountTraining({
            status: "PENDING",
            view: "prepare",
            formValues
        });

        return (wrapper.vm as unknown as { optionsComplete: boolean }).optionsComplete;
    };

    it("is false on an untouched form — a run needs a trust and a confirmed dataset", () => {
        expect(optionsComplete({})).toBe(false);
    });

    it("is false with trusts but no enrichment confirmation", () => {
        expect(optionsComplete({ trust_ids: ["trust-a"] })).toBe(false);
    });

    it("is false with enrichment confirmed but no trust to train on", () => {
        expect(optionsComplete({
            enriched: "true",
            trust_ids: []
        })).toBe(false);
    });

    it("is true once a trust is chosen and enrichment is confirmed", () => {
        expect(optionsComplete({
            enriched: "true",
            trust_ids: ["trust-a"]
        })).toBe(true);
    });

    it("accepts the single-trust case, where the field holds a bare id", () => {
        expect(optionsComplete({
            enriched: "true",
            trust_ids: "trust-a"
        })).toBe(true);
    });
});

// The tests above stub the vee-validate Form. This one drives the real one, because
// `optionsComplete` rests on Form exposing a reactive `values` off its ref — an
// assumption a stub cannot check.
describe("Training reads the real vee-validate form", () => {
    function mountWithRealForm() {
        return mount(Training, {
            global: {
                plugins: [
                    createTestingPinia({
                        createSpy: vi.fn,
                        stubActions: false,
                        initialState: {
                            project: {
                                project: {
                                    id: "p-1",
                                    approvedTrusts: [{
                                        id: "trust-a",
                                        name: "KCH",
                                        approved: true
                                    }]
                                }
                            }
                        }
                    })
                ],
                stubs: {
                    AiCard: { template: "<div><slot /></div>" },
                    AiAlert: alertStub
                }
            },
            props: {
                canTrain: true,
                status: "PENDING" as const,
                allFilesUploaded: true,
                requiredFiles: [],
                uploadedFileNames: [],
                jobType: "standard",
                view: "prepare" as const
            }
        });
    }

    const complete = (wrapper: ReturnType<typeof mountWithRealForm>) =>
        (wrapper.vm as unknown as { optionsComplete: boolean }).optionsComplete;

    it("tracks the live form values as the switches are flipped", async () => {
        const wrapper = mountWithRealForm();
        await flushPromises();

        expect(complete(wrapper)).toBe(false);

        const switches = wrapper.findAll("button[role=\"switch\"]");
        expect(switches.length).toBe(2);

        // Enrichment alone is not enough.
        await switches[0].trigger("click");
        await flushPromises();
        expect(complete(wrapper)).toBe(false);

        // ...and now a trust.
        await switches[1].trigger("click");
        await flushPromises();
        expect(complete(wrapper)).toBe(true);

        // Unselecting the trust closes it again.
        await switches[1].trigger("click");
        await flushPromises();
        expect(complete(wrapper)).toBe(false);
    });

    it("still blocks a submit that bypasses the disabled button: the schema is the real gate", async () => {
        // The page disables Initiate Training on optionsComplete, but the form is
        // natively submittable (Enter key, or optionsComplete drifting from the
        // schema). This drives the exposed submit path with a missing trust and
        // pins that the schema itself rejects it and its message reaches the user.
        const { initialiseTraining } = await import("@/services/model-service");
        vi.mocked(initialiseTraining).mockClear();

        const wrapper = mountWithRealForm();
        await flushPromises();

        // Only enrichment checked — no trust selected.
        await wrapper.findAll("button[role=\"switch\"]")[0].trigger("click");
        await flushPromises();

        await wrapper.find("form").trigger("submit");

        // yup validation resolves on a macrotask, so poll rather than flush.
        await vi.waitFor(() => {
            expect(wrapper.text()).toContain("You must select a minimum of one trust for training.");
        });
        expect(vi.mocked(initialiseTraining)).not.toHaveBeenCalled();
    });
});

describe("Training job-types failure", () => {
    // The required-files list is per-backend, so when it can't be loaded there is nothing honest
    // to show. The card says so rather than falling through to copy that implies we know.
    it("renders an error alert instead of the required-files messaging", () => {
        const wrapper = mountTraining({
            jobTypesError: true,
            requiredFiles: []
        });
        const alert = wrapper.find("[data-test=job-types-error-alert]");

        expect(alert.exists()).toBe(true);
        expect(alert.attributes("data-variant")).toBe("error");
        expect(wrapper.text()).not.toContain("required files are");
        expect(wrapper.text()).not.toContain("All required model files must be uploaded");
        expect(wrapper.text()).not.toContain("Missing:");
    });

    it("emits retryJobTypes once when the retry action is clicked", async () => {
        const wrapper = mountTraining({
            jobTypesError: true,
            requiredFiles: []
        });

        await wrapper.find("[data-test=job-types-error-alert] [data-test=alert-action]").trigger("click");

        expect(wrapper.emitted("retryJobTypes")).toHaveLength(1);
    });

    it("drops the retry affordance while a retry is in flight", () => {
        const wrapper = mountTraining({
            jobTypesError: true,
            jobTypesLoading: true,
            requiredFiles: []
        });
        const alert = wrapper.find("[data-test=job-types-error-alert]");

        expect(alert.find("[data-test=alert-action]").exists()).toBe(false);
        expect(alert.text()).toContain("Retrying");
    });

    it("shows nothing once the model has been dispatched", () => {
        // Past PENDING the file set is settled and the card belongs to the run, so a failure to
        // load the required files is no longer something the user can act on.
        const wrapper = mountTraining({
            jobTypesError: true,
            status: "RUNNING",
            view: "prepare"
        });

        expect(wrapper.find("[data-test=job-types-error-alert]").exists()).toBe(false);
    });

    it("shows nothing on the run view", () => {
        const wrapper = mountTraining({
            jobTypesError: true,
            status: "RUNNING",
            view: "run"
        });

        expect(wrapper.find("[data-test=job-types-error-alert]").exists()).toBe(false);
    });
});
