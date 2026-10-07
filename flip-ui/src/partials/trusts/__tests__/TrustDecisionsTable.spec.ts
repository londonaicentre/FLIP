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
import { describe, expect, it } from "vitest";

import TrustDecisionsTable from "@/partials/trusts/TrustDecisionsTable.vue";
import type { ITrustDecision } from "@/services/trust-service";
import { shortDate } from "@/utils/trust-decisions";

const decided = (overrides: Partial<ITrustDecision> = {}): ITrustDecision => ({
    projectId: "p1",
    projectName: "Spleen segmentation v2",
    description: "Federated spleen segmentation on portal-venous CT.",
    ownerName: "Demo Researcher",
    projectStatus: "APPROVED",
    hasImaging: true,
    stagedAt: "2026-09-25T10:00:00.000Z",
    query: "SELECT person_id FROM omop.person LIMIT 10",
    cohort: {
        recordCount: 142,
        suppressed: false,
        error: null
    },
    status: "APPROVED",
    decidedByName: "Ada Admin",
    decidedAt: "2026-09-26T09:00:00.000Z",
    decidedAs: "SITE",
    ...overrides
});

// Rendered through the runtime's en-GB locale ("Sep" or "Sept"), so expectations use the same formatter.
const STAGED = shortDate("2026-09-25T10:00:00.000Z");
const DECIDED = shortDate("2026-09-26T09:00:00.000Z");

const stubs = {
    "router-link": {
        props: ["to"],
        template: "<a :data-to='to' v-bind='$attrs'><slot /></a>"
    }
};

const mountTable = (decisions: ITrustDecision[]) =>
    mount(TrustDecisionsTable, {
        props: { decisions },
        global: { stubs }
    });

describe("TrustDecisionsTable (FLIP#1258)", () => {
    it("shows each decision with its owner, cohort, kind, pill and decider", () => {
        const row = mountTable([decided()]).find("[data-test='decision-row']");

        expect(row.text()).toContain(`Demo Researcher · staged ${STAGED}`);
        expect(row.text()).toContain("142 records · imaging");
        expect(row.find("[data-test='decision-pill']").text()).toBe("Approved");
        expect(row.text()).toContain(`Ada Admin · ${DECIDED}`);
        expect(row.text()).not.toContain("(hub)");
    });

    it("marks a decision the hub made before the trust had a Trust Admin", () => {
        const row = mountTable([decided({
            status: "DECLINED",
            decidedAs: "HUB"
        })]).find("[data-test='decision-row']");

        expect(row.find("[data-test='decision-pill']").text()).toBe("Declined");
        expect(row.text()).toContain(`Ada Admin (hub) · ${DECIDED}`);
    });

    it("shows only what it knows of an unattributed decision and a sparse project", () => {
        const row = mountTable([decided({
            ownerName: null,
            stagedAt: null,
            hasImaging: false,
            decidedByName: null
        })]).find("[data-test='decision-row']");

        expect(row.text()).toContain("Unknown owner");
        expect(row.text()).not.toContain("staged");
        expect(row.text()).toContain("· tabular");
        expect(row.text()).toContain(DECIDED);
        expect(row.text()).not.toContain("Ada Admin");
    });

    it("leaves the decider line empty when neither the decider nor the date is known", () => {
        const row = mountTable([decided({
            status: "APPROVED",
            decidedByName: null,
            decidedAt: null
        })]).find("[data-test='decision-row']");

        expect(row.find("[data-test='decision-pill']").text()).toBe("Approved");
        expect(row.text()).not.toContain(DECIDED);
    });

    it("expands a row to its details and collapses it again", async () => {
        const wrapper = mountTable([decided()]);
        const toggle = wrapper.find("[data-test='decision-row-toggle']");

        await toggle.trigger("click");
        expect(toggle.attributes("aria-expanded")).toBe("true");
        expect(wrapper.find("[data-test='decision-details']").text()).toContain("Imaging: Yes");

        await toggle.trigger("click");
        expect(toggle.attributes("aria-expanded")).toBe("false");
        expect(wrapper.find("[data-test='decision-details']").exists()).toBe(false);
    });

    it("expands a tabular row with no description or query to just its imaging line", async () => {
        const wrapper = mountTable([decided({
            description: "",
            hasImaging: false,
            query: null
        })]);

        await wrapper.find("[data-test='decision-row-toggle']").trigger("click");

        const details = wrapper.find("[data-test='decision-details']");
        expect(details.text()).toBe("Imaging: No — tabular data only");
        expect(details.find("[data-test='view-query-btn']").exists()).toBe(false);
    });
});
