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

import AiAlert from "@/components/AiAlert/AiAlert.vue";
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

function mountStaging(stageableTrustIds: string[] | undefined, trusts = [trustA, trustB, trustNew]) {
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false
    });
    const wrapper = mount(ProjectStaging, {
        props: {
            hasQuery: true,
            staging: false,
            stageableTrustIds
        },
        global: { plugins: [pinia] }
    });

    const trustStore = useTrustStore();
    trustStore.trusts = trusts;

    return wrapper;
}

describe("ProjectStaging — stageableTrustIds gating", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("only renders trusts in the stageable set (responded successfully)", async () => {
        // Stageable = trusts that responded without error. Late-joiners,
        // never-responded, and errored trusts are all excluded by the
        // parent before the prop is passed.
        const wrapper = mountStaging([trustA.id, trustB.id]);
        await flushPromises();

        expect(wrapper.find(`[data-test="${trustA.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustB.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustNew.name}-selector"]`).exists()).toBe(false);
    });

    it("shows every trust when stageableTrustIds is undefined (parent still loading)", async () => {
        const wrapper = mountStaging(undefined);
        await flushPromises();

        expect(wrapper.find(`[data-test="${trustA.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustB.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustNew.name}-selector"]`).exists()).toBe(true);
    });

    it("hides a trust the parent excluded (errored or never-responded)", async () => {
        // The parent computes `respondedTrustIds − erroredTrustIds`. From the
        // component's POV both cases look the same: missing from the prop.
        const wrapper = mountStaging([trustA.id]);
        await flushPromises();

        expect(wrapper.find(`[data-test="${trustA.name}-selector"]`).exists()).toBe(true);
        expect(wrapper.find(`[data-test="${trustB.name}-selector"]`).exists()).toBe(false);
    });
});

describe("ProjectStaging — empty staging-list messaging", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("shows an informational message (not an error) when trusts loaded but none returned cohort results", async () => {
        // stageableTrustIds is empty -> every loaded trust is filtered out, but
        // the trust list itself loaded fine. A normal empty state, not a failure.
        const wrapper = mountStaging([]);
        await flushPromises();

        const alert = wrapper.findComponent(AiAlert);
        expect(alert.props("variant")).toBe("info");
        expect(alert.props("text")).toContain("returned cohort results");
        expect(wrapper.text()).not.toContain("Unable to load Trusts");
    });

    it("shows the error message when no trusts could be loaded at all", async () => {
        const wrapper = mountStaging([], []);
        await flushPromises();

        const alert = wrapper.findComponent(AiAlert);
        expect(alert.props("variant")).toBe("error");
        expect(alert.props("text")).toContain("Unable to load Trusts");
    });
});
