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

let swrvKey: (() => string) | undefined;

vi.mock("swrv", () => ({
    default: (key: () => string) => {
        swrvKey = key;

        return {
            data: mockSwrvData,
            mutate: vi.fn(),
            error: mockSwrvError
        };
    }
}));

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

const frozenSnapshot = (overrides: Partial<ICohortSnapshot> = {}): ICohortSnapshot => ({
    trustId: "trust-1",
    trustName: "Alpha Trust",
    status: "frozen",
    error: null,
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
        swrvKey = undefined;
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

    it("warns on a frozen trust whose last re-check failed, without calling it refused", () => {
        mockSwrvData.value = [frozenSnapshot({
            error: "The last re-check failed (The trust did not report a result in time); "
                + "this trust may no longer hold its frozen membership"
        })];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-count']").exists()).toBe(true);
        expect(wrapper.find("[data-test='cohort-snapshot-recheck']").text()).toContain("last re-check failed");
        expect(wrapper.find("[data-test='cohort-snapshot-failed-text']").exists()).toBe(false);
    });

    it("shows no re-check warning on a frozen trust without one", () => {
        mockSwrvData.value = [frozenSnapshot()];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-recheck']").exists()).toBe(false);
    });

    it("shows a pending trust with an amber chip and no counts", () => {
        mockSwrvData.value = [frozenSnapshot({
            status: "pending",
            rowCount: null,
            approvedRecordCount: null,
            hasAccessions: null,
            snapshotAt: null
        })];
        const wrapper = mountComponent();

        const row = wrapper.find("[data-test='cohort-snapshot-row']");
        expect(row.attributes("data-status")).toBe("pending");
        expect(wrapper.find("[data-test='cohort-snapshot-pending']").exists()).toBe(true);
        expect(row.text()).toContain("training at this trust will be refused until it is");
        expect(wrapper.find("[data-test='cohort-snapshot-count']").exists()).toBe(false);
        expect(wrapper.find("[data-test='cohort-snapshot-tabular']").exists()).toBe(false);
    });

    it("shows a failed trust in red with its category-only reason", () => {
        mockSwrvData.value = [frozenSnapshot({
            status: "failed",
            error: "Refused by the trust (for example, the cohort is below its disclosure threshold)",
            rowCount: null,
            approvedRecordCount: null,
            hasAccessions: null,
            snapshotAt: null
        })];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-failed']").exists()).toBe(true);
        expect(wrapper.find("[data-test='cohort-snapshot-failed-text']").text())
            .toBe("Cohort not frozen — training at this trust will be refused");
        expect(wrapper.find("[data-test='cohort-snapshot-error']").text()).toContain("disclosure threshold");
        expect(wrapper.find("[data-test='cohort-snapshot-count']").exists()).toBe(false);
    });

    it("omits the reason line when a failed trust has none", () => {
        mockSwrvData.value = [frozenSnapshot({
            status: "failed",
            rowCount: null
        })];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-failed']").exists()).toBe(true);
        expect(wrapper.find("[data-test='cohort-snapshot-error']").exists()).toBe(false);
    });

    it("lists frozen, pending and failed trusts side by side", () => {
        mockSwrvData.value = [
            frozenSnapshot(),
            frozenSnapshot({
                trustId: "trust-2",
                trustName: "Beta Trust",
                status: "pending",
                rowCount: null
            }),
            frozenSnapshot({
                trustId: "trust-3",
                trustName: "Gamma Trust",
                status: "failed",
                rowCount: null
            })
        ];
        const wrapper = mountComponent();

        const statuses = wrapper.findAll("[data-test='cohort-snapshot-row']").map((row) => row.attributes("data-status"));
        expect(statuses).toEqual(["frozen", "pending", "failed"]);
    });

    it("fetches the project's snapshots only once loading is allowed", () => {
        mountComponent();
        expect(swrvKey?.()).toBe("/projects/test-project-id/cohort-snapshots");

        mountComponent(false);
        expect(swrvKey?.()).toBe("");
    });

    it("shows a dash for a frozen trust whose count and date are not reported", () => {
        mockSwrvData.value = [frozenSnapshot({
            rowCount: null,
            snapshotAt: null
        })];
        const wrapper = mountComponent();

        expect(wrapper.find("[data-test='cohort-snapshot-count']").text()).toBe("— records · —");
        expect(wrapper.find("[data-test='cohort-snapshot-drift']").exists()).toBe(false);
    });

    it("renders nothing when loading is gated off (project not approved yet)", () => {
        mockSwrvData.value = [frozenSnapshot()];
        const wrapper = mountComponent(false);

        expect(wrapper.find("[data-test='cohort-snapshot-row']").exists()).toBe(false);
    });
});
