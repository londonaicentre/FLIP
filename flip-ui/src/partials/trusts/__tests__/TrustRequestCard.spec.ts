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

import TrustRequestCard from "@/partials/trusts/TrustRequestCard.vue";
import type { ITrustDecision } from "@/services/trust-service";
import { shortDate } from "@/utils/trust-decisions";

const request = (overrides: Partial<ITrustDecision> = {}): ITrustDecision => ({
    projectId: "p1",
    projectName: "Spleen segmentation v2",
    description: "Federated spleen segmentation on portal-venous CT.",
    ownerName: "Demo Researcher",
    projectStatus: "STAGED",
    hasImaging: true,
    stagedAt: "2026-09-25T10:00:00.000Z",
    query: "SELECT person_id FROM omop.person LIMIT 10",
    cohort: {
        recordCount: 142,
        suppressed: false,
        error: null
    },
    status: "PENDING",
    decidedByName: null,
    decidedAt: null,
    decidedAs: null,
    ...overrides
});

// Rendered through the runtime's en-GB locale ("Sep" or "Sept"), so expectations use the same formatter.
const STAGED = shortDate("2026-09-25T10:00:00.000Z");

const stubs = {
    "router-link": {
        props: ["to"],
        template: "<a :data-to='to' v-bind='$attrs'><slot /></a>"
    }
};

const mountCard = (decision: ITrustDecision, busy = false) =>
    mount(TrustRequestCard, {
        props: {
            decision,
            busy
        },
        global: { stubs }
    });

describe("TrustRequestCard (FLIP#1258)", () => {
    it("shows an imaging request with its owner, staging date, cohort and a link to the query", () => {
        const wrapper = mountCard(request());

        expect(wrapper.text()).toContain(`Demo Researcher · staged ${STAGED}`);
        expect(wrapper.text()).toContain("Federated spleen segmentation on portal-venous CT.");
        expect(wrapper.find("[data-test='request-cohort']").text()).toBe("142");
        expect(wrapper.find("[data-test='request-imaging']").text()).toBe("Yes");
        expect(wrapper.find("[data-test='imaging-notice']").exists()).toBe(true);
        expect(wrapper.find("[data-test='view-query-btn']").attributes("data-to")).toBe("/project/p1/cohort-query");
    });

    it("falls back when the owner, staging date, description and query are missing", () => {
        const wrapper = mountCard(request({
            ownerName: null,
            stagedAt: null,
            description: "",
            query: null,
            cohort: null
        }));

        expect(wrapper.text()).toContain("Unknown owner");
        expect(wrapper.text()).not.toContain("staged");
        expect(wrapper.text()).not.toContain("Federated spleen segmentation");
        expect(wrapper.find("[data-test='view-query-btn']").exists()).toBe(false);
        expect(wrapper.text()).toContain("No cohort query");
        expect(wrapper.find("[data-test='request-cohort-note']").text()).toBe("not reported");
    });

    it("marks a tabular request as imaging-free, with no imaging notice", () => {
        const wrapper = mountCard(request({ hasImaging: false }));

        expect(wrapper.find("[data-test='request-imaging']").text()).toBe("No");
        expect(wrapper.text()).toContain("Tabular data only");
        expect(wrapper.find("[data-test='imaging-notice']").exists()).toBe(false);
    });

    it("emits the decision the Trust Admin picks", async () => {
        const wrapper = mountCard(request());

        await wrapper.find("[data-test='approve-btn']").trigger("click");
        await wrapper.find("[data-test='decline-btn']").trigger("click");

        expect(wrapper.emitted("decide")).toEqual([["approve"], ["decline"]]);
    });

    it("disables both decisions while one is being saved", () => {
        const wrapper = mountCard(request(), true);

        expect(wrapper.find("button[data-test='approve-btn']").attributes("disabled")).toBeDefined();
        expect(wrapper.find("button[data-test='decline-btn']").attributes("disabled")).toBeDefined();
    });
});
