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

import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DerivedFromSelect from "@/partials/models/DerivedFromSelect.vue";

const mockGetModels = vi.fn();

vi.mock("@/services/model-service", () => ({ getModels: (...args: unknown[]) => mockGetModels(...args) }));

const models = [
    {
        id: "fingerprint",
        name: "Fingerprint"
    },
    {
        id: "training",
        name: "Training"
    }
];

const mountSelect = async (props = {}) => {
    const wrapper = mount(DerivedFromSelect, {
        props: {
            projectId: "proj-1",
            modelValue: null,
            ...props
        }
    });

    await flushPromises();

    return wrapper;
};

describe("DerivedFromSelect", () => {
    beforeEach(() => {
        mockGetModels.mockReset();
        mockGetModels.mockResolvedValue({ data: models });
    });

    it("offers the project's other models and no link by default", async () => {
        const wrapper = await mountSelect({ excludeId: "training" });

        expect(mockGetModels).toHaveBeenCalledWith("/projects/proj-1/models?pageSize=100");
        expect(wrapper.findAll("option").map(option => option.text())).toEqual(["None", "Fingerprint"]);
        expect((wrapper.find("select").element as HTMLSelectElement).value).toBe("");
    });

    it("emits the chosen model, and null when the link is cleared", async () => {
        const wrapper = await mountSelect({ modelValue: "fingerprint" });

        expect((wrapper.find("select").element as HTMLSelectElement).value).toBe("fingerprint");

        await wrapper.find("select").setValue("training");
        await wrapper.find("select").setValue("");

        expect(wrapper.emitted("update:modelValue")).toEqual([["training"], [null]]);
    });

    it("stays out of the form when the project has no other model", async () => {
        mockGetModels.mockResolvedValue({
            data: [{
                id: "training",
                name: "Training"
            }]
        });

        const wrapper = await mountSelect({ excludeId: "training" });

        expect(wrapper.find("select").exists()).toBe(false);
    });

    it("stays out of the form when the models cannot be listed", async () => {
        mockGetModels.mockRejectedValue(new Error("offline"));

        const wrapper = await mountSelect();

        expect(wrapper.find("select").exists()).toBe(false);
    });
});
