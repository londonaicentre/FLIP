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


import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import TrainingActionsMenu from "@/partials/models/TrainingActionsMenu.vue";
import { ModelStatusEnum, stopTraining } from "@/services/model-service";

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
        stopTraining: vi.fn(),
        getDownloadUrlForResults: vi.fn()
    };
});

const mockSnackbarError = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        error: (...args: unknown[]) => mockSnackbarError(...args),
        success: vi.fn(),
        warning: vi.fn(),
        show: vi.fn()
    }
}));

function mountMenu(status: ModelStatusEnum = ModelStatusEnum.RUNNING) {
    return mount(TrainingActionsMenu, {
        global: {
            stubs: {
                AiButton: {
                    inheritAttrs: false,
                    props: ["disabled"],
                    template: "<button v-bind=\"$attrs\" :disabled='disabled'><slot /></button>"
                },
                AiConfirmModal: {
                    props: ["dialog", "confirmationText", "closeButtonText", "continueButtonText",
                        "continueAction", "submitting"],
                    template: "<div data-test='confirm-modal-stub' />"
                }
            }
        },
        props: { status }
    });
}

describe("TrainingActionsMenu responsive button labels", () => {
    // Below lg the labels hide so the buttons shrink to icons and stop
    // squashing the model title in the page header; title + aria-label keep
    // the buttons named for tooltips and screen readers at every width.
    const labelSelector = "span.hidden.lg\\:inline";

    it("hides the Stop Training label below lg and keeps title + aria-label", () => {
        const wrapper = mountMenu();

        const btn = wrapper.find("[data-test=stop-training-btn]");
        expect(btn.exists()).toBe(true);
        expect(btn.attributes("aria-label")).toBe("Stop Training");
        expect(btn.attributes("title")).toBe("Stop Training");
        const label = btn.find(labelSelector);
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("Stop Training");
    });

    it("hides the Download Results label below lg and keeps an aria-label", () => {
        const wrapper = mountMenu(ModelStatusEnum.RESULTS_UPLOADED);

        const btn = wrapper.find("[data-test=download-results-btn]");
        expect(btn.exists()).toBe(true);
        expect(btn.attributes("aria-label")).toBe("Download Results");
        const label = btn.find(labelSelector);
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("Download Results");
    });
});

describe("TrainingActionsMenu abort vs stop affordance", () => {
    const labelSelector = "span.hidden.lg\\:inline";

    it.each([
        ["STOPPED", ModelStatusEnum.STOPPED],
        ["ERROR", ModelStatusEnum.ERROR]
    ])("disables the button with Stop Training copy at %s", (_name, status) => {
        const wrapper = mountMenu(status);

        const btn = wrapper.find("[data-test=stop-training-btn]");
        expect(btn.attributes("disabled")).toBeDefined();
        expect(btn.attributes("title")).toBe("Stop Training");
        expect(btn.attributes("aria-label")).toBe("Stop Training");
        expect(btn.find(labelSelector).text()).toBe("Stop Training");
    });

    it("enables the button as Abort job while the model is queued (INITIATED)", () => {
        const wrapper = mountMenu(ModelStatusEnum.INITIATED);

        const btn = wrapper.find("[data-test=stop-training-btn]");
        expect(btn.attributes("disabled")).toBeUndefined();
        expect(btn.attributes("title")).toBe("Abort job");
        expect(btn.attributes("aria-label")).toBe("Abort job");
        expect(btn.find(labelSelector).text()).toBe("Abort job");
    });

    it("uses abort copy in the confirm modal at INITIATED", () => {
        const wrapper = mountMenu(ModelStatusEnum.INITIATED);

        const modal = wrapper.findComponent("[data-test=confirm-modal-stub]");
        expect(modal.props("confirmationText")).toContain("abort");
        expect(modal.props("continueButtonText")).toBe("Abort job");
    });

    it.each([
        ["PREPARED", ModelStatusEnum.PREPARED],
        ["RUNNING", ModelStatusEnum.RUNNING]
    ])("keeps the enabled Stop Training affordance at %s", (_name, status) => {
        const wrapper = mountMenu(status);

        const btn = wrapper.find("[data-test=stop-training-btn]");
        expect(btn.attributes("disabled")).toBeUndefined();
        expect(btn.attributes("title")).toBe("Stop Training");
        expect(btn.find(labelSelector).text()).toBe("Stop Training");

        const modal = wrapper.findComponent("[data-test=confirm-modal-stub]");
        expect(modal.props("confirmationText")).toBe("Are you sure you want to stop training this model?");
        expect(modal.props("continueButtonText")).toBe("Stop Training");
    });

    it.each([
        ["STOPPED", ModelStatusEnum.STOPPED],
        ["ERROR", ModelStatusEnum.ERROR],
        ["INITIATED", ModelStatusEnum.INITIATED]
    ])("keeps Download Results disabled at %s — only RESULTS_UPLOADED enables it", (_name, status) => {
        const wrapper = mountMenu(status);

        expect(wrapper.find("[data-test=download-results-btn]").attributes("disabled")).toBeDefined();
    });

    it("aborts via the confirm modal at INITIATED", async () => {
        vi.mocked(stopTraining).mockResolvedValueOnce(undefined);
        const wrapper = mountMenu(ModelStatusEnum.INITIATED);
        const modal = () => wrapper.findComponent("[data-test=confirm-modal-stub]");

        expect(modal().props("dialog")).toBe(false);
        await wrapper.find("[data-test=stop-training-btn]").trigger("click");
        expect(modal().props("dialog")).toBe(true);

        await (modal().props("continueAction") as () => Promise<void>)();
        expect(vi.mocked(stopTraining)).toHaveBeenCalledWith("model-1");
        expect(modal().props("dialog")).toBe(false);
    });

    it("reports 'Failed to abort job' when the pre-running abort fails", async () => {
        vi.mocked(stopTraining).mockRejectedValueOnce(new Error("network error"));
        const wrapper = mountMenu(ModelStatusEnum.INITIATED);
        const modal = () => wrapper.findComponent("[data-test=confirm-modal-stub]");

        await wrapper.find("[data-test=stop-training-btn]").trigger("click");
        await (modal().props("continueAction") as () => Promise<void>)();

        expect(mockSnackbarError).toHaveBeenCalledWith({
            title: "Something went wrong!",
            text: "Failed to abort job"
        });
        expect(modal().props("dialog")).toBe(false);
    });

    it("reports 'Failed to stop training' when stopping a running model fails", async () => {
        vi.mocked(stopTraining).mockRejectedValueOnce(new Error("network error"));
        const wrapper = mountMenu(ModelStatusEnum.RUNNING);
        const modal = () => wrapper.findComponent("[data-test=confirm-modal-stub]");

        await wrapper.find("[data-test=stop-training-btn]").trigger("click");
        await (modal().props("continueAction") as () => Promise<void>)();

        expect(mockSnackbarError).toHaveBeenCalledWith({
            title: "Something went wrong!",
            text: "Failed to stop training"
        });
        expect(modal().props("dialog")).toBe(false);
    });
});
