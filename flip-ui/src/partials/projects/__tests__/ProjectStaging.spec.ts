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
    code: "TA",
    region: null,
    last_heartbeat: null,
    project_count: 0
};
const trustB = {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Trust B",
    code: "TB",
    region: null,
    last_heartbeat: null,
    project_count: 0
};
// `trustNew` represents a trust that joined the platform after the cohort
// query was run — it has no QueryResult, so it must not appear in the staging
// selector even though it shows up in the global trust list.
const trustNew = {
    id: "33333333-3333-3333-3333-333333333333",
    name: "Trust New",
    code: "TN",
    region: null,
    last_heartbeat: null,
    project_count: 0
};

function mountStaging(stageableTrustIds: string[] | undefined, trusts = [trustA, trustB, trustNew], hasQuery = true) {
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false,
        // The Stage button is v-if="!isViewer", i.e. needs CanCreateProjects.
        initialState: { auth: { user: { permissions: ["CanCreateProjects"] } } }
    });
    const wrapper = mount(ProjectStaging, {
        props: {
            hasQuery,
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

// The staging card swaps into the same left-column slot as ProjectApproval, so it
// carries the same fill-height contract (FLIP#1168): the page column is
// lg:overflow-hidden, so a content-height card with no internal scroller would
// clip a long roster — and the Stage Project button under it — with no way to
// reach it. Same assertions as ProjectApproval.spec's layout test.
describe("ProjectStaging — fill-height layout contract", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("stretches the card to its slot, scrolls the roster internally and pins the Stage button", async () => {
        const wrapper = mountStaging([trustA.id, trustB.id]);
        await flushPromises();

        // Without h-full the card sizes to content inside its lg:flex-1 wrapper and
        // never reaches the shared baseline the other three cards end on.
        const card = wrapper.element;
        expect(card.className).toContain("flex");
        expect(card.className).toContain("flex-col");
        expect(card.className).toContain("h-full");

        // Every link in the chain has to be a flex column for flex-1 to mean anything.
        const form = wrapper.find("form");
        expect(form.classes()).toEqual(expect.arrayContaining(["flex", "flex-col", "flex-1", "min-h-0"]));
        const roster = wrapper.find("ul[role=list]").element.parentElement;
        expect(roster?.parentElement?.className).toContain("flex-col");

        const scroller = roster;
        expect(scroller?.className).toContain("overflow-y-auto");
        expect(scroller?.className).toContain("flex-1");
        expect(scroller?.className).toContain("min-h-0");

        // The button lives outside the scroller so it never scrolls out of reach.
        const stage = wrapper.find("[data-test=stage-project-btn]").element.closest("div.p-4");
        expect(stage?.className).toContain("shrink-0");
        expect(stage?.className).toContain("mt-auto");
        expect(scroller?.contains(stage)).toBe(false);
    });

    it("does not centre the query-required alert in the fill-height card", async () => {
        const wrapper = mountStaging(undefined, [trustA, trustB, trustNew], false);
        await flushPromises();

        // m-auto absorbs the free space of the flex column and floats the alert to the
        // middle of the card (the regression ProjectStatus and LatestModels also avoid).
        const alert = wrapper.find("[data-test=query-required-alert]");
        expect(alert.exists()).toBe(true);
        expect(alert.classes()).not.toContain("m-auto");
        expect(alert.classes()).toContain("shrink-0");
    });
});
