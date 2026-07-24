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

import { mountComponent } from "@test/helper";
import { describe, expect, it, vi } from "vitest";

import ModelUpload from "@/partials/models/ModelUpload.vue";
import { JobType } from "@/services/model-service";

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({
            params: {},
            query: {}
        })
    };
});

vi.mock("@/services/file-service", () => ({
    deleteModelFile: vi.fn(),
    downloadModelFile: vi.fn(),
    processScannedFile: vi.fn()
}));

const confirmModalStub = { template: "<div data-test=\"confirm-modal-stub\"><slot name=\"confirmation\" /></div>" };

describe("ModelUpload delete-confirm slot", () => {
    it("renders the file-deletion confirmation slot via AiConfirmModal", async () => {
        const wrapper = mountComponent(ModelUpload, {
            global: { stubs: { AiConfirmModal: confirmModalStub } },
            props: {
                files: [],
                loading: false,
                canUpload: true,
                modelId: "model-1",
                requiredFiles: ["trainer.py"],
                jobType: "standard" as JobType
            }
        });

        const html = wrapper.html();

        expect(html).toContain("data-test=\"confirm-modal-stub\"");
        expect(html).toContain("Are you sure you wish to delete");
        expect(html).toContain("This file will not be available as part of model training");
    });

    it("escapes file names in the confirmation slot", async () => {
        const wrapper = mountComponent(ModelUpload, {
            global: { stubs: { AiConfirmModal: confirmModalStub } },
            props: {
                files: [],
                loading: false,
                canUpload: true,
                modelId: "model-1",
                requiredFiles: ["trainer.py"],
                jobType: "standard" as JobType
            }
        });

        const payload = "<img src=x onerror=alert(1)>";

        (wrapper.vm as unknown as { fileToDelete: string }).fileToDelete = payload;
        await wrapper.vm.$nextTick();

        const html = wrapper.html();

        expect(html).not.toContain("<img src=x onerror");
        expect(html).toContain("&lt;img");
    });
});
