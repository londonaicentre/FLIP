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
import { ModelStatusEnum } from "@/services/model-service";

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

function mountMenu(status: ModelStatusEnum = ModelStatusEnum.RUNNING) {
    return mount(TrainingActionsMenu, {
        global: {
            stubs: {
                AiButton: {
                    inheritAttrs: false,
                    props: ["disabled"],
                    template: "<button v-bind=\"$attrs\" :disabled='disabled'><slot /></button>"
                },
                AiConfirmModal: { template: "<div data-test='confirm-modal-stub' />" }
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
