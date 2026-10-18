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
import { describe, expect, test, vi } from "vitest";

import type { IProjectTrust } from "@/services/project-service";

import ProjectApproval from "../ProjectApproval.vue";

interface MountOptions {
    approvedTrusts?: IProjectTrust[];
    projectApproved?: boolean;
    approving?: boolean;
    canApprove?: boolean;
    permissions?: string[];
    trustAdminOf?: { id: string; code: string; name: string } | null;
}

const PENDING_TRUSTS: IProjectTrust[] = [
    {
        id: "t1",
        name: "Kings College Hospital",
        code: "KCH",
        status: "PENDING"
    },
    {
        id: "t2",
        name: "UCLH",
        code: "UCH",
        status: "PENDING"
    }
];

function mountProjectApproval({
    approvedTrusts = PENDING_TRUSTS,
    projectApproved = false,
    approving = false,
    canApprove = true,
    permissions = ["CanApproveProjects"],
    trustAdminOf = null
}: MountOptions = {}) {
    return mount(ProjectApproval, {
        props: {
            approvedTrusts,
            projectApproved,
            approving,
            canApprove
        },
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                permissions,
                                trustAdminOf
                            }
                        }
                    }
                })
            ]
        }
    });
}

// AiButton puts data-test on its native <button>, inside a tooltip wrapper <div>.
const saveButton = (wrapper: ReturnType<typeof mountProjectApproval>) =>
    wrapper.find("button[data-test=approve-project-btn]");

describe("ProjectApproval", () => {
    test("sorts trusts alphabetically by code (KCH before UCH)", async () => {
        const wrapper = mountProjectApproval({
            approvedTrusts: [
                {
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    status: "PENDING"
                },
                {
                    id: "t2",
                    name: "Kings College Hospital",
                    code: "KCH",
                    status: "PENDING"
                }
            ]
        });
        await flushPromises();

        const rows = wrapper.findAll("li");
        expect(rows[0].text()).toContain("KCH");
        expect(rows[1].text()).toContain("UCH");
    });

    test("falls back to trust.name when no code is set, and still sorts by the fallback label", async () => {
        const wrapper = mountProjectApproval({
            approvedTrusts: [
                {
                    id: "t1",
                    name: "Zeta Trust",
                    status: "PENDING"
                },
                {
                    id: "t2",
                    name: "Alpha Trust",
                    status: "PENDING"
                }
            ]
        });
        await flushPromises();

        const rows = wrapper.findAll("li");
        expect(rows[0].text()).toContain("Alpha Trust");
        expect(rows[1].text()).toContain("Zeta Trust");
    });

    describe("while the project is staged, for an approver", () => {
        test("offers Approve and Decline for every trust, neither pressed yet", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            for (const idx of [0, 1]) {
                expect(wrapper.find(`[data-test=trust-approve-${idx}]`).attributes("aria-pressed")).toBe("false");
                expect(wrapper.find(`[data-test=trust-decline-${idx}]`).attributes("aria-pressed")).toBe("false");
            }
            expect(saveButton(wrapper).exists()).toBe(true);
        });

        test("pressing Approve selects approve for that trust only", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("aria-pressed")).toBe("true");
            expect(wrapper.find("[data-test=trust-decline-0]").attributes("aria-pressed")).toBe("false");
            expect(wrapper.find("[data-test=trust-approve-1]").attributes("aria-pressed")).toBe("false");
        });

        test("the last choice on a row wins", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            await wrapper.find("[data-test=trust-decline-0]").trigger("click");

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("aria-pressed")).toBe("false");
            expect(wrapper.find("[data-test=trust-decline-0]").attributes("aria-pressed")).toBe("true");
        });

        test("saves one trust's decision while the others stay pending (FLIP#1258)", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();
            expect(saveButton(wrapper).attributes("disabled")).toBeDefined();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            expect(saveButton(wrapper).attributes("disabled")).toBeUndefined();
            await saveButton(wrapper).trigger("click");

            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: ["t1"],
                declined: []
            }]]);
        });

        test("Save emits the approved and the declined trusts, sorted by row", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-decline-0]").trigger("click");
            await wrapper.find("[data-test=trust-approve-1]").trigger("click");
            await saveButton(wrapper).trigger("click");

            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: ["t2"],
                declined: ["t1"]
            }]]);
        });

        test("declining every trust is a valid save", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-decline-0]").trigger("click");
            await wrapper.find("[data-test=trust-decline-1]").trigger("click");
            await saveButton(wrapper).trigger("click");

            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: [],
                declined: ["t1", "t2"]
            }]]);
        });

        test("disables every choice and Save while a save is in flight", async () => {
            const wrapper = mountProjectApproval({ approving: true });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("disabled")).toBeDefined();
            expect(wrapper.find("[data-test=trust-decline-0]").attributes("disabled")).toBeDefined();
            expect(saveButton(wrapper).attributes("disabled")).toBeDefined();
        });

        test("after every trust declined, starts from the saved decisions and saves only once one changes", async () => {
            const wrapper = mountProjectApproval({
                approvedTrusts: [
                    {
                        id: "t1",
                        name: "Kings College Hospital",
                        code: "KCH",
                        status: "DECLINED",
                        decidedByName: "Ada Approver",
                        decidedAt: "2026-05-26T10:00:00Z"
                    },
                    {
                        id: "t2",
                        name: "UCLH",
                        code: "UCH",
                        status: "DECLINED",
                        decidedByName: "Ada Approver",
                        decidedAt: "2026-05-26T10:00:00Z"
                    }
                ]
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-decline-0]").attributes("aria-pressed")).toBe("true");
            expect(wrapper.find("[data-test=trust-decision-0]").text()).toContain("Declined by Ada Approver");
            expect(saveButton(wrapper).attributes("disabled")).toBeDefined();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            await saveButton(wrapper).trigger("click");

            // Only the changed trust is sent; UCH's recorded decline stands untouched.
            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: ["t1"],
                declined: []
            }]]);
        });
    });

    // The layout re-fetches the open project every few seconds, which hands the card a fresh trusts array
    // each time. An approver who takes longer than that must not lose their unsaved choices.
    describe("when the project is re-fetched mid-edit", () => {
        const refetched = (trusts: IProjectTrust[]) => trusts.map(t => ({ ...t }));

        test("keeps unsaved choices when nothing was saved in between", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            await wrapper.find("[data-test=trust-decline-1]").trigger("click");
            await wrapper.setProps({ approvedTrusts: refetched(PENDING_TRUSTS) });

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("aria-pressed")).toBe("true");
            expect(wrapper.find("[data-test=trust-decline-1]").attributes("aria-pressed")).toBe("true");
            await saveButton(wrapper).trigger("click");
            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: ["t1"],
                declined: ["t2"]
            }]]);
        });

        test("keeps an unsaved change to a saved decision", async () => {
            const declined: IProjectTrust[] = PENDING_TRUSTS.map(t => ({
                ...t,
                status: "DECLINED"
            }));
            const wrapper = mountProjectApproval({ approvedTrusts: declined });
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            await wrapper.setProps({ approvedTrusts: refetched(declined) });

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("aria-pressed")).toBe("true");
            expect(saveButton(wrapper).attributes("disabled")).toBeUndefined();
        });

        test("takes up a decision someone else saved, without touching the other rows", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-0]").trigger("click");
            await wrapper.setProps({
                approvedTrusts: [
                    { ...PENDING_TRUSTS[0] },
                    {
                        ...PENDING_TRUSTS[1],
                        status: "DECLINED",
                        decidedByName: "Other Approver"
                    }
                ]
            });

            expect(wrapper.find("[data-test=trust-approve-0]").attributes("aria-pressed")).toBe("true");
            expect(wrapper.find("[data-test=trust-decline-1]").attributes("aria-pressed")).toBe("true");
        });

        test("a saved decision overrides an unsaved choice on the same row", async () => {
            const wrapper = mountProjectApproval();
            await flushPromises();

            await wrapper.find("[data-test=trust-approve-1]").trigger("click");
            await wrapper.setProps({
                approvedTrusts: [
                    { ...PENDING_TRUSTS[0] },
                    {
                        ...PENDING_TRUSTS[1],
                        status: "DECLINED",
                        decidedByName: "Other Approver"
                    }
                ]
            });

            expect(wrapper.find("[data-test=trust-approve-1]").attributes("aria-pressed")).toBe("false");
            expect(wrapper.find("[data-test=trust-decline-1]").attributes("aria-pressed")).toBe("true");
        });
    });

    describe("trusts that decide for themselves (FLIP#1258)", () => {
        const SITE_RUN: IProjectTrust[] = [
            {
                id: "t1",
                name: "Kings College Hospital",
                code: "KCH",
                status: "PENDING"
            },
            {
                id: "t2",
                name: "UCLH",
                code: "UCH",
                status: "PENDING",
                hasTrustAdmin: true
            }
        ];

        test("the hub admin sees a trust with a Trust Admin read-only, awaiting that Trust Admin", async () => {
            const wrapper = mountProjectApproval({ approvedTrusts: SITE_RUN });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(true);
            expect(wrapper.find("[data-test=trust-approve-1]").exists()).toBe(false);
            expect(wrapper.find("[data-test=trust-status-chip-1]").text()).toContain("Pending");
            expect(wrapper.find("[data-test=trust-decision-1]").text()).toBe("Awaiting UCH's Trust Admin");
        });

        test("that trust's own Trust Admin can decide it, and only it", async () => {
            const wrapper = mountProjectApproval({
                approvedTrusts: SITE_RUN,
                permissions: ["CanCreateProjects"],
                trustAdminOf: {
                    id: "t2",
                    code: "UCH",
                    name: "UCLH"
                }
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(false);
            await wrapper.find("[data-test=trust-decline-1]").trigger("click");
            await saveButton(wrapper).trigger("click");
            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: [],
                declined: ["t2"]
            }]]);
        });

        test("names a site's decision as its Trust Admin's", async () => {
            const wrapper = mountProjectApproval({
                projectApproved: true,
                approvedTrusts: [{
                    id: "t2",
                    name: "UCLH",
                    code: "UCH",
                    status: "APPROVED",
                    hasTrustAdmin: true,
                    decidedAs: "SITE",
                    decidedByName: "Tia Trustadmin",
                    decidedAt: "2026-05-26T10:00:00Z"
                }]
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-decision-0]").text()).toMatch(
                /^Approved by Tia Trustadmin \(UCH's Trust Admin\) · .*May/
            );
        });

        test("offers no Save when the viewer can decide none of the trusts", async () => {
            const wrapper = mountProjectApproval({ approvedTrusts: [SITE_RUN[1]] });
            await flushPromises();

            expect(saveButton(wrapper).exists()).toBe(false);
        });
    });

    describe("read-only views", () => {
        test("a user without CanApproveProjects sees each trust's status and no choices", async () => {
            const wrapper = mountProjectApproval({ permissions: [] });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(false);
            expect(wrapper.find("[data-test=trust-decline-0]").exists()).toBe(false);
            expect(saveButton(wrapper).exists()).toBe(false);
            expect(wrapper.find("[data-test=trust-status-chip-0]").text()).toContain("Pending");
        });

        test("an approved project shows who approved and who declined, and when", async () => {
            const wrapper = mountProjectApproval({
                projectApproved: true,
                approvedTrusts: [
                    {
                        id: "t1",
                        name: "Kings College Hospital",
                        code: "KCH",
                        status: "APPROVED",
                        decidedBy: "u1",
                        decidedByName: "Ada Approver",
                        decidedAt: "2026-05-26T10:00:00Z"
                    },
                    {
                        id: "t2",
                        name: "UCLH",
                        code: "UCH",
                        status: "DECLINED",
                        decidedBy: "u1",
                        decidedByName: "Ada Approver",
                        decidedAt: "2026-05-26T10:00:00Z"
                    }
                ]
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(false);
            expect(saveButton(wrapper).exists()).toBe(false);

            const approved = wrapper.find("[data-test=trust-status-chip-0]");
            expect(approved.text()).toContain("Approved");
            expect(approved.attributes("data-status")).toBe("APPROVED");
            // toLocaleDateString({ day: "numeric", month: "short" }) for 26 May 2026 → "May 26" (en-US).
            expect(wrapper.find("[data-test=trust-decision-0]").text()).toMatch(/Approved by Ada Approver · .*May/);
            expect(approved.attributes("title")).toContain("Kings College Hospital approved by Ada Approver on");

            const declined = wrapper.find("[data-test=trust-status-chip-1]");
            expect(declined.text()).toContain("Declined");
            expect(declined.attributes("data-status")).toBe("DECLINED");
            expect(wrapper.find("[data-test=trust-decision-1]").text()).toContain("Declined by Ada Approver");
        });

        test("an approval recorded before decisions were attributed shows its date with no decider", async () => {
            const wrapper = mountProjectApproval({
                projectApproved: true,
                approvedTrusts: [{
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    status: "APPROVED",
                    decidedAt: "2026-05-26T10:00:00Z"
                }]
            });
            await flushPromises();

            const line = wrapper.find("[data-test=trust-decision-0]").text();
            expect(line).toMatch(/^Approved · .*May/);
            expect(line).not.toContain("by");
            expect(wrapper.find("[data-test=trust-status-chip-0]").attributes("title")).toMatch(/^UCLH approved on /);
        });

        test("a trust still pending on an approved project can still be decided (FLIP#1258)", async () => {
            const wrapper = mountProjectApproval({
                projectApproved: true,
                approvedTrusts: [
                    {
                        id: "t1",
                        name: "Kings College Hospital",
                        code: "KCH",
                        status: "APPROVED",
                        decidedByName: "Ada Approver"
                    },
                    {
                        id: "t2",
                        name: "UCLH",
                        code: "UCH",
                        status: "PENDING"
                    }
                ]
            });
            await flushPromises();

            // The approved trust's decision is final; the pending one keeps its choices.
            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(false);
            expect(wrapper.find("[data-test=trust-status-chip-0]").text()).toContain("Approved");
            await wrapper.find("[data-test=trust-approve-1]").trigger("click");
            await saveButton(wrapper).trigger("click");
            expect(wrapper.emitted("approveProject")).toEqual([[{
                approved: ["t2"],
                declined: []
            }]]);
        });

        test("shows a trust closed at upgrade as not approved, with no decision recorded (FLIP#1258)", async () => {
            // Left out of a project approved before per-trust decisions: DECLINED with no decider or date.
            const wrapper = mountProjectApproval({
                projectApproved: true,
                approvedTrusts: [{
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    status: "DECLINED"
                }]
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-status-chip-0]").text()).toBe("Not approved");
            expect(wrapper.find("[data-test=trust-decision-0]").text()).toBe("No decision recorded");
        });

        test("says the project stays staged once every trust declined", async () => {
            const wrapper = mountProjectApproval({
                permissions: [],
                approvedTrusts: PENDING_TRUSTS.map(t => ({
                    ...t,
                    status: "DECLINED"
                }))
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-all-declined]").text()).toContain("stays staged");
        });

        test("renders no choices when the project is neither staged nor approved", async () => {
            const wrapper = mountProjectApproval({
                canApprove: false,
                projectApproved: false
            });
            await flushPromises();

            expect(wrapper.find("[data-test=trust-approve-0]").exists()).toBe(false);
            expect(saveButton(wrapper).exists()).toBe(false);
        });
    });

    test("counts approved and declined trusts in the header eyebrow", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: true,
            approvedTrusts: [
                {
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    status: "APPROVED"
                },
                {
                    id: "t2",
                    name: "Kings College Hospital",
                    code: "KCH",
                    status: "DECLINED",
                    decidedByName: "Ada Admin",
                    decidedAt: "2026-09-01T12:00:00.000Z"
                },
                // Closed at upgrade, not declined: left out of the count.
                {
                    id: "t3",
                    name: "Guy's and St Thomas'",
                    code: "GST",
                    status: "DECLINED"
                }
            ]
        });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-approval-count]").text()).toBe("1 of 3 approved · 1 declined");
    });

    test("leaves declined out of the eyebrow when no trust declined", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: true,
            approvedTrusts: [
                {
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    status: "APPROVED"
                },
                {
                    id: "t2",
                    name: "Kings College Hospital",
                    code: "KCH",
                    status: "APPROVED"
                }
            ]
        });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-approval-count]").text()).toBe("2 of 2 approved");
    });

    test("omits the eyebrow when the project has no trusts", async () => {
        const wrapper = mountProjectApproval({ approvedTrusts: [] });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-approval-count]").exists()).toBe(false);
    });

    test("scrolls the trust list internally and pins the save button to the card bottom", async () => {
        const wrapper = mountProjectApproval();
        await flushPromises();

        // The scroller wraps the <ul> so its border-y stays put at the top of the
        // scroll area rather than scrolling away with the first row.
        const scroller = wrapper.find("ul[role=list]").element.parentElement;
        expect(scroller?.className).toContain("overflow-y-auto");
        expect(scroller?.className).toContain("flex-1");
        expect(scroller?.className).toContain("min-h-0");

        const save = saveButton(wrapper).element.closest("div.p-4");
        expect(save?.className).toContain("shrink-0");
        expect(save?.className).toContain("mt-auto");
    });
});
