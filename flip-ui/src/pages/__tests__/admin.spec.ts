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
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import AdminPage from "@/pages/admin.vue";

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRouter: () => ({
            currentRoute: ref({ fullPath: "/admin/users" }),
            push: vi.fn()
        })
    };
});

vi.mock("@/router", () => ({ routeChange: { viewProjects: vi.fn() } }));

function mountAdmin() {
    return mount(AdminPage, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "admin",
                                userId: "1",
                                attributes: {
                                    sub: "1",
                                    email: "a@e.co"
                                },
                                permissions: ["CanAccessAdminPanel"]
                            },
                            signInStep: "DONE"
                        }
                    }
                })
            ],
            stubs: { "router-view": { template: "<div />" } }
        }
    });
}

describe("admin page mobile tab-select band", () => {
    // On squashed viewports the sidebar collapses into a <select>; its wrapper
    // band is white in light mode but must go transparent in dark mode — a
    // hard-coded bg-white otherwise paints a white bar across the dark page.
    it("makes the band behind the tab select transparent in dark mode", () => {
        const wrapper = mountAdmin();

        const band = wrapper.find("nav[aria-label=Breadcrumb]");
        expect(band.exists()).toBe(true);
        expect(band.classes()).toContain("bg-white");
        expect(band.classes()).toContain("dark:bg-transparent");
    });
});

describe("admin page gutters", () => {
    // 64px of horizontal padding on top of the card's own is a third of a
    // phone viewport; drop to 16px below md (design handoff fix #5).
    it("uses compact gutters on mobile and full gutters from md up", () => {
        const wrapper = mountAdmin();

        const content = wrapper.find("[data-test='admin-content']");
        expect(content.exists()).toBe(true);
        expect(content.classes()).toContain("px-4");
        expect(content.classes()).toContain("py-4");
        expect(content.classes()).toContain("md:px-8");
        expect(content.classes()).toContain("md:py-8");
        expect(content.classes()).not.toContain("px-8");
    });
});
