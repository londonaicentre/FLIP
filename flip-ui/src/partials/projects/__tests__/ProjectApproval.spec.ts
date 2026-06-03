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

import ProjectApproval from "../ProjectApproval.vue";

// AiGuard renders its slot only when the user has the listed permissions —
// stub it to always render the slot so the button gate's other conditions
// (canApprove, !projectApproved) are what we test here.
vi.mock("@/components/AiGuard/AiGuard.vue", () => ({
    default: {
        name: "AiGuard",
        template: "<div><slot /></div>"
    }
}));

// AiSwitch internally calls vee-validate's useField, which needs a Form
// context — and our stubbed <Form> below doesn't provide one. Use a thin
// stub whose attributes reflect the props the parent sets, so the tests
// can still assert presence + disabled state per trust row.
vi.mock("@/components/AiSwitch/AiSwitch.vue", () => ({
    default: {
        name: "AiSwitch",
        props: ["name", "value", "disabled", "label", "hideError"],
        template: "<input type=\"checkbox\" :value=\"value\" :disabled=\"disabled\" />",
        inheritAttrs: true
    }
}));

// vee-validate's <Form> binds a hidden helper that complicates checkbox state
// in jsdom — stub to a thin wrapper that calls @submit with the latest values
// the test stages via $emit("submit", payload).
vi.mock("vee-validate", () => ({
    Form: {
        name: "FormStub",
        emits: ["submit"],
        template: "<form @submit.prevent=\"$emit('submit', $attrs.payload)\"><slot :errors=\"{}\" /></form>"
    }
}));

interface MountOptions {
    approvedTrusts?: Array<{ id: string; name: string; code?: string; approved?: boolean; approvedAt?: string }>;
    projectApproved?: boolean;
    approving?: boolean;
    canApprove?: boolean;
    permissions?: string[];
}

function mountProjectApproval({
    approvedTrusts = [
        {
            id: "t1",
            name: "Kings College Hospital",
            code: "KCH"
        },
        {
            id: "t2",
            name: "UCLH",
            code: "UCH"
        }
    ],
    projectApproved = false,
    approving = false,
    canApprove = true,
    permissions = ["CanApproveProjects"]
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
                    initialState: { auth: { user: { permissions } } }
                })
            ]
        }
    });
}

describe("ProjectApproval", () => {
    test("sorts trusts alphabetically by code (KCH before UCH)", async () => {
        const wrapper = mountProjectApproval({
            approvedTrusts: [
                {
                    id: "t1",
                    name: "UCLH",
                    code: "UCH"
                },
                {
                    id: "t2",
                    name: "Kings College Hospital",
                    code: "KCH"
                }
            ]
        });
        await flushPromises();

        const rows = wrapper.findAll("li");
        // Per-row label rendering shows trust.code (or name fallback).
        expect(rows[0].text()).toContain("KCH");
        expect(rows[1].text()).toContain("UCH");
    });

    test("falls back to trust.name when no code is set, and still sorts by the fallback label", async () => {
        const wrapper = mountProjectApproval({
            approvedTrusts: [
                {
                    id: "t1",
                    name: "Zeta Trust"
                },
                {
                    id: "t2",
                    name: "Alpha Trust"
                }
            ]
        });
        await flushPromises();

        const rows = wrapper.findAll("li");
        expect(rows[0].text()).toContain("Alpha Trust");
        expect(rows[1].text()).toContain("Zeta Trust");
    });

    test("shows the staged-toggle (AiSwitch) and the Approve button while the project is awaiting approval", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: false,
            canApprove: true
        });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-staged-0]").exists()).toBe(true);
        expect(wrapper.find("[data-test=trust-staged-1]").exists()).toBe(true);
        expect(wrapper.find("[data-test=approve-project-btn]").exists()).toBe(true);
    });

    test("disables the staged-toggle when the user lacks CanApproveProjects", async () => {
        const wrapper = mountProjectApproval({ permissions: [] });
        await flushPromises();

        // AiSwitch forwards `disabled` to the underlying input (see the stub at
        // the top of this file). The component sets disabled when
        // !hasPermissionToApprove OR approving — with no permissions, the first
        // branch fires and the AiSwitch root receives the attribute.
        const sw = wrapper.find("[data-test=trust-staged-0]");
        expect(sw.exists()).toBe(true);
        expect(sw.attributes("disabled")).toBeDefined();
    });

    test("hides the toggle and shows an approval chip once the project is approved (with the approval date)", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: true,
            approvedTrusts: [
                {
                    id: "t1",
                    name: "Kings College Hospital",
                    code: "KCH",
                    approved: true,
                    approvedAt: "2026-05-26T10:00:00Z"
                }
            ]
        });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-staged-0]").exists()).toBe(false);
        const chip = wrapper.find("[data-test=trust-approved-chip-0]");
        expect(chip.exists()).toBe(true);
        // toLocaleDateString({ day: "numeric", month: "short" }) for 26 May 2026 → "26 May".
        // (Test environment uses en-US locale by default.)
        expect(chip.text()).toMatch(/May/);
        expect(chip.attributes("title")).toContain("Kings College Hospital approved on");
    });

    test("shows the Not Approved badge for trusts that opted out after approval", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: true,
            approvedTrusts: [
                {
                    id: "t1",
                    name: "UCLH",
                    code: "UCH",
                    approved: false
                }
            ]
        });
        await flushPromises();

        expect(wrapper.find("[data-test=trust-approved-chip-0]").exists()).toBe(false);
        expect(wrapper.text()).toContain("Not Approved");
    });

    test("hides the Approve button while the project is approved", async () => {
        const wrapper = mountProjectApproval({ projectApproved: true });
        await flushPromises();

        expect(wrapper.find("[data-test=approve-project-btn]").exists()).toBe(false);
    });

    test("renders no form at all when the project is neither approvable nor approved", async () => {
        const wrapper = mountProjectApproval({
            canApprove: false,
            projectApproved: false
        });
        await flushPromises();

        expect(wrapper.find("form").exists()).toBe(false);
        expect(wrapper.find("[data-test=approve-project-btn]").exists()).toBe(false);
    });

    test("falls back to 'Approved' on the chip when no approvedAt is set", async () => {
        const wrapper = mountProjectApproval({
            projectApproved: true,
            approvedTrusts: [{
                id: "t1",
                name: "UCLH",
                code: "UCH",
                approved: true
            }]
        });
        await flushPromises();

        const chip = wrapper.find("[data-test=trust-approved-chip-0]");
        expect(chip.text()).toContain("Approved");
        expect(chip.attributes("title")).toBe("UCLH approved");
    });
});
