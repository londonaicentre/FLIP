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
import { nextTick, ref } from "vue";

import type { ITrustDecision } from "@/services/trust-service";

import MyTrustPage from "../my-trust.vue";

const trustsRef = ref<unknown[] | undefined>(undefined);
const decisionsRef = ref<ITrustDecision[] | undefined>(undefined);
const decisionsErrorRef = ref<unknown>(null);
const mutateDecisions = vi.fn();

vi.mock("swrv", () => ({
    default: (keyFn: () => string | null) => {
        const key = typeof keyFn === "function" ? keyFn() : keyFn;
        // A null key fetches nothing, as in swrv itself.
        if (key === null) {
            return {
                data: ref(undefined),
                mutate: vi.fn(),
                error: ref(null)
            };
        }
        if (typeof key === "string" && key.includes("/decisions")) {
            return {
                data: decisionsRef,
                mutate: mutateDecisions,
                error: decisionsErrorRef
            };
        }
        if (key === "hub-health") {
            return {
                data: ref({ version: "v0.9.0" }),
                mutate: vi.fn(),
                error: ref(null)
            };
        }

        return {
            data: trustsRef,
            mutate: vi.fn(),
            error: ref(null)
        };
    }
}));

const mockApproveProject = vi.fn();
vi.mock("@/services/project-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/project-service")>();

    return {
        ...actual,
        approveProject: (...args: unknown[]) => mockApproveProject(...args)
    };
});

const mockSnackbarSuccess = vi.fn();
const mockSnackbarError = vi.fn();
const mockSnackbarWarning = vi.fn();
vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: (...args: unknown[]) => mockSnackbarSuccess(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args),
        show: vi.fn(),
        warning: (...args: unknown[]) => mockSnackbarWarning(...args)
    }
}));

const mockViewProjects = vi.fn();
vi.mock("@/router", () => ({
    routeChange: { viewProjects: (...args: unknown[]) => mockViewProjects(...args) },
    default: { push: vi.fn() }
}));

const TRUST_ADMIN_OF = {
    id: "dta",
    code: "DTA",
    name: "Decision Trust A"
};

const pending = (overrides: Partial<ITrustDecision> = {}): ITrustDecision => ({
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

const stubs = {
    "router-link": {
        props: ["to"],
        template: "<a :data-to='to' v-bind='$attrs'><slot /></a>"
    },
    TrustDetailCard: {
        props: ["trust", "hubVersion"],
        template: "<div data-test='trust-card'>{{ trust.name }}</div>"
    },
    AiConfirmModal: {
        props: ["dialog", "continueAction", "confirmationText", "title", "continueButtonText"],
        emits: ["close-modal"],
        template: `<div v-if="dialog" data-test="confirm-modal">
            <p data-test="confirm-text">{{ confirmationText }}</p>
            <button data-test="confirm-modal-btn" @click="continueAction">{{ continueButtonText }}</button>
            <button data-test="cancel-modal-btn" @click="$emit('close-modal')">Cancel</button>
        </div>`
    }
};

function mountPage(trustAdminOf: typeof TRUST_ADMIN_OF | null = TRUST_ADMIN_OF) {
    return mount(MyTrustPage, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: {
                        user: {
                            permissions: ["CanCreateProjects"],
                            trustAdminOf
                        }
                    }
                }
            })],
            stubs
        }
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    decisionsErrorRef.value = null;
    trustsRef.value = [
        {
            id: "dta",
            name: "Decision Trust A",
            code: "DTA",
            region: "London",
            last_heartbeat: new Date().toISOString(),
            project_count: 2,
            services: {},
            services_updated_at: null
        }
    ];
    decisionsRef.value = [
        pending(),
        pending({
            projectId: "p2",
            projectName: "EHR risk prediction",
            status: "APPROVED",
            projectStatus: "APPROVED",
            decidedByName: "Ada Admin",
            decidedAt: "2026-09-01T12:00:00.000Z",
            decidedAs: "SITE"
        })
    ];
});

describe("My Trust", () => {
    it("heads the page with the Trust Admin eyebrow, title and description", async () => {
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='my-trust-eyebrow']").text()).toBe("Trust admin · DTA");
        expect(wrapper.find("h1").text()).toBe("My Trust");
        const description = wrapper.find("[data-test='my-trust-description']");
        expect(description.text().replace(/\s+/g, " ")).toBe(
            "Review project requests to use data held at Decision Trust A, and monitor the health of the local FLIP node."
        );
        expect(description.find("strong").text()).toBe("Decision Trust A");
    });

    it("shows the Trust Admin's own trust card beside the decisions", async () => {
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='trust-card']").text()).toBe("Decision Trust A");
        const html = wrapper.html();
        expect(html.indexOf("data-test=\"trust-card\"")).toBeGreaterThan(html.indexOf("data-test=\"decided-list\""));
    });

    it("sends anyone who is not a Trust Admin back to Projects", async () => {
        mountPage(null);
        await flushPromises();

        expect(mockViewProjects).toHaveBeenCalled();
    });

    it("counts each section beside its heading", async () => {
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='pending-heading']").text()).toContain("Awaiting your decision");
        expect(wrapper.find("[data-test='pending-count']").text()).toBe("1");
        expect(wrapper.find("[data-test='decided-count']").text()).toBe("1");
    });

    it("shows a pending request as a card with its facts, a query link and the decision buttons", async () => {
        const wrapper = mountPage();
        await nextTick();

        const card = wrapper.find("[data-test='pending-list'] [data-test='request-card']");
        expect(card.text()).toContain("Spleen segmentation v2");
        expect(card.text()).toContain("Demo Researcher · staged");
        expect(card.text()).toContain("Awaiting decision");
        expect(card.find("[data-test='request-cohort']").text()).toBe("142");
        expect(card.find("[data-test='request-imaging']").text()).toBe("Yes");
        expect(card.find("[data-test='imaging-notice']").text()).toContain(
            "Approving starts the imaging pull from PACS to XNAT."
        );
        // The query is a link to the project's cohort-query page, not inline SQL.
        expect(card.find("pre").exists()).toBe(false);
        expect(card.find("[data-test='view-query-btn']").attributes("data-to")).toBe("/project/p1/cohort-query");
        expect(card.find("[data-test='approve-btn']").exists()).toBe(true);
        expect(card.find("[data-test='decline-btn']").exists()).toBe(true);
    });

    it("has no imaging notice for a tabular project", async () => {
        decisionsRef.value = [pending({ hasImaging: false })];
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='request-imaging']").text()).toBe("No");
        expect(wrapper.find("[data-test='imaging-notice']").exists()).toBe(false);
    });

    it.each([
        [null, "—", "not reported"],
        [{
            recordCount: 0,
            suppressed: true,
            error: null
        }, "—", "below the disclosure threshold"],
        [{
            recordCount: null,
            suppressed: false,
            error: "boom"
        }, "—", "query failed"]
    ])("reports a cohort of %j as %s %s", async (cohort, value, note) => {
        decisionsRef.value = [pending({ cohort })];
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='request-cohort']").text()).toBe(value);
        expect(wrapper.find("[data-test='request-cohort-note']").text()).toBe(note);
    });

    it("lists decided projects as table rows toned by their decision", async () => {
        const wrapper = mountPage();
        await nextTick();

        const row = wrapper.find("[data-test='decided-list'] [data-test='decision-row']");
        expect(row.text()).toContain("EHR risk prediction");
        expect(row.find("[data-test='decision-pill']").text()).toBe("Approved");
        expect(row.find("[data-test='decision-rail']").classes()).toContain("bg-emerald-500");
        expect(row.text()).toContain("Ada Admin");
    });

    it("shows a trust closed at upgrade as not approved rather than declined", async () => {
        decisionsRef.value = [pending({
            projectId: "p9",
            projectName: "Legacy project",
            status: "DECLINED",
            projectStatus: "APPROVED"
        })];
        const wrapper = mountPage();
        await nextTick();

        const row = wrapper.find("[data-test='decision-row']");
        expect(row.find("[data-test='decision-pill']").text()).toBe("Not approved");
        expect(row.text()).toContain("No decision recorded");
    });

    it("expands a decided row to its description and a link to the query", async () => {
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='decision-row-toggle']").trigger("click");

        const details = wrapper.find("[data-test='decision-details']");
        expect(details.text()).toContain("Federated spleen segmentation");
        expect(details.find("[data-test='view-query-btn']").attributes("data-to")).toBe("/project/p2/cohort-query");
    });

    it("approves only for this trust after confirming, then refreshes the list", async () => {
        mockApproveProject.mockResolvedValue({ projectStatus: "APPROVED" });
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='approve-btn']").trigger("click");
        expect(wrapper.find("[data-test='confirm-text']").text()).toContain(
            "Approve Spleen segmentation v2 for Decision Trust A?"
        );
        await wrapper.find("[data-test='confirm-modal-btn']").trigger("click");
        await flushPromises();

        expect(mockApproveProject).toHaveBeenCalledWith("/step/project/p1/approve", {
            approved: ["dta"],
            declined: []
        });
        expect(mutateDecisions).toHaveBeenCalled();
        expect(mockSnackbarSuccess).toHaveBeenCalled();
    });

    it("records nothing when the confirmation is cancelled", async () => {
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='approve-btn']").trigger("click");
        await wrapper.find("[data-test='cancel-modal-btn']").trigger("click");

        expect(wrapper.find("[data-test='confirm-modal']").exists()).toBe(false);
        expect(mockApproveProject).not.toHaveBeenCalled();
    });

    it("says the decision was not saved when the request fails, and closes the confirmation", async () => {
        mockApproveProject.mockRejectedValue(new Error("Network Error"));
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='decline-btn']").trigger("click");
        await wrapper.find("[data-test='confirm-modal-btn']").trigger("click");
        await flushPromises();

        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Decision not saved" }));
        expect(mockSnackbarSuccess).not.toHaveBeenCalled();
        expect(mutateDecisions).not.toHaveBeenCalled();
        expect(wrapper.find("[data-test='confirm-modal']").exists()).toBe(false);
    });

    it("declines only for this trust after confirming", async () => {
        mockApproveProject.mockResolvedValue({ projectStatus: "STAGED" });
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='decline-btn']").trigger("click");
        await wrapper.find("[data-test='confirm-modal-btn']").trigger("click");
        await flushPromises();

        expect(mockApproveProject).toHaveBeenCalledWith("/step/project/p1/approve", {
            approved: [],
            declined: ["dta"]
        });
    });

    it("shows the empty-state card when nothing awaits a decision", async () => {
        decisionsRef.value = [];
        const wrapper = mountPage();
        await nextTick();

        const empty = wrapper.find("[data-test='nothing-pending']");
        expect(empty.text()).toContain("There are no requests awaiting your decision");
        expect(empty.text()).toContain("New project requests for this Trust will appear here.");
        expect(wrapper.find("[data-test='pending-list']").exists()).toBe(false);
        expect(wrapper.find("[data-test='pending-count']").text()).toBe("0");
    });

    it("does not claim an empty queue while the decisions are still loading", async () => {
        decisionsRef.value = undefined;
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='nothing-pending']").exists()).toBe(false);
        expect(wrapper.find("[data-test='decisions-loading']").exists()).toBe(true);
    });

    it("says the decisions could not be loaded instead of showing an empty queue", async () => {
        decisionsRef.value = undefined;
        decisionsErrorRef.value = new Error("403");
        const wrapper = mountPage();
        await nextTick();

        expect(wrapper.find("[data-test='nothing-pending']").exists()).toBe(false);
        expect(wrapper.find("[data-test='decisions-error']").text()).toContain("could not be loaded");
    });

    it("warns when approval could not start imaging at this trust", async () => {
        mockApproveProject.mockResolvedValue({
            projectStatus: "APPROVED",
            successful: false,
            details: [{
                trust: "Decision Trust A",
                success: false,
                message: "boom"
            }]
        });
        const wrapper = mountPage();
        await nextTick();

        await wrapper.find("[data-test='approve-btn']").trigger("click");
        await wrapper.find("[data-test='confirm-modal-btn']").trigger("click");
        await flushPromises();

        expect(mockSnackbarWarning).toHaveBeenCalled();
        expect(mockSnackbarSuccess).not.toHaveBeenCalled();
    });
});
