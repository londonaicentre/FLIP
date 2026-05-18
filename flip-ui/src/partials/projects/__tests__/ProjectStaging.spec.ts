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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useTrustStore } from "@/store/trusts";

import ProjectStaging from "../ProjectStaging.vue";

const trustA = {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Trust A",
    code: "TA"
};
const trustB = {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Trust B",
    code: "TB"
};
// `trustNew` represents a trust that joined the platform after the cohort
// query was run — it has no QueryResult, so it must not appear in the staging
// selector even though it shows up in the global trust list.
const trustNew = {
    id: "33333333-3333-3333-3333-333333333333",
    name: "Trust New",
    code: "TN"
};

function mountStaging(queriedTrustIds: string[] | undefined) {
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false
    });
    const wrapper = mount(ProjectStaging, {
        props: {
            hasQuery: true,
            staging: false,
            queriedTrustIds
        },
        global: { plugins: [pinia] }
    });

    const trustStore = useTrustStore();
    trustStore.trusts = [trustA, trustB, trustNew];

    return wrapper;
}

describe("ProjectStaging — queriedTrustIds gating", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("hides trusts that did not participate in the cohort query", async () => {
        const wrapper = mountStaging([trustA.id, trustB.id]);
        await flushPromises();

        expect(wrapper.find(`[data-test="${trustA.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustB.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustNew.name}-selector"]`).exists()).toBe(false);
    });

    it("shows every trust when queriedTrustIds is undefined (parent still loading)", async () => {
        const wrapper = mountStaging(undefined);
        await flushPromises();

        expect(wrapper.find(`[data-test="${trustA.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustB.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustNew.name}-selector"]`).exists()).toBe(true);
    });
});
