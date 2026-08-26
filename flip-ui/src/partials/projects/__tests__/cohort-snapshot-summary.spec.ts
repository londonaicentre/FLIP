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
import { reactive, ref } from "vue";

import { ICohortSnapshot } from "@/services/project-service";

import CohortSnapshotSummary from "../CohortSnapshotSummary.vue";

const mockRoute = reactive({
    name: "ProjectView",
    fullPath: "/project/test-project-id",
    path: "/project/test-project-id",
    params: { projectId: "test-project-id" } as Record<string, string>
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const mockSwrvData = ref<ICohortSnapshot[] | undefined>(undefined);
const mockSwrvError = ref<Error | null>(null);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: mockSwrvError
    })
}));

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

const frozenSnapshot = (overrides: Partial<ICohortSnapshot> = {}): ICohortSnapshot => ({
    trustId: "trust-1",
    trustName: "Alpha Trust",
    rowCount: 300,
    approvedRecordCount: 300,
    hasAccessions: true,
    snapshotAt: "2026-08-26T21:16:00+00:00",
    queryId: "query-1",
    ...overrides
});

const mountComponent = (canLoad = true) =>
    mount(CohortSnapshotSummary, { props: { canLoad } });

describe("CohortSnapshotSummary", () => {
    beforeEach(() => {
        mockSwrvData.value = undefined;
        mockSwrvError.value = null;
    });

    it("renders nothing while there are no snapshot records", () => {
        mockSwrvData.value = [];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-row']").exists()).toBe(false);
    });

    it("renders one row per trust with the frozen record count", () => {
        mockSwrvData.value = [
            frozenSnapshot(),
            frozenSnapshot({
                trustId: "trust-2",
                trustName: "Beta Trust",
                rowCount: 120
            })
        ];
        const wrapper = mountComponent();

        const rows = wrapper.findAll("[data-test='cohort-snapshot-row']");
        expect(rows).toHaveLength(2);
        expect(rows[0].text()).toContain("Alpha Trust");
        expect(rows[0].text()).toContain("300");
        expect(rows[1].text()).toContain("Beta Trust");
        expect(rows[1].text()).toContain("120");
    });

    it("surfaces membership drift when the frozen count differs from the approved count", () => {
        mockSwrvData.value = [frozenSnapshot({
            rowCount: 300,
            approvedRecordCount: 280
        })];
        const wrapper = mountComponent();

        const drift = wrapper.find("[data-test='cohort-snapshot-drift']");
        expect(drift.exists()).toBe(true);
        expect(drift.text()).toContain("280");
    });

    it("shows no drift badge when frozen equals approved or approved is unknown", () => {
        mockSwrvData.value = [
            frozenSnapshot(),
            frozenSnapshot({
                trustId: "trust-2",
                trustName: "Beta Trust",
                approvedRecordCount: null
            })
        ];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-drift']").exists()).toBe(false);
    });

    it("marks a tabular cohort (no accession column) so an empty imaging panel is explained", () => {
        mockSwrvData.value = [frozenSnapshot({ hasAccessions: false })];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-tabular']").exists()).toBe(true);
    });

    it("renders nothing when loading is gated off (project not approved yet)", () => {
        mockSwrvData.value = [frozenSnapshot()];
        const wrapper = mountComponent(false);

        expect(wrapper.find("[data-test='cohort-snapshot-row']").exists()).toBe(false);
    });
});
