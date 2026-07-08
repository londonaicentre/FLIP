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
            stubs: {
                "router-view": { template: "<div />" },
                // Expose `to` as an attribute so tests can assert the link target.
                "router-link": {
                    template: "<a :data-to='to'><slot /></a>",
                    props: ["to"]
                }
            }
        }
    });
}

describe("admin chip-tab nav", () => {
    // The xl-only left sidebar and the mobile <select> band are replaced by one
    // segmented pill nav under the top bar, rendered at every breakpoint.
    it("renders the chip-tab nav at all breakpoints", () => {
        const wrapper = mountAdmin();

        const nav = wrapper.find("nav[aria-label='Admin sections']");
        expect(nav.exists()).toBe(true);
        // Chips that don't fit wrap onto the next line (the models filter-tiles
        // idiom) rather than scrolling horizontally out of view.
        expect(nav.classes()).toContain("flex-wrap");
        expect(nav.classes()).not.toContain("overflow-x-auto");
        expect(nav.classes()).toContain("gap-2");
        expect(nav.find("div.rounded-lg").exists()).toBe(false);
        // The chips float on the page background — no divider band under them.
        expect(nav.classes()).not.toContain("border-b");
        expect(nav.classes()).not.toContain("hidden");
        expect(nav.classes()).not.toContain("xl:hidden");
        expect(nav.classes()).not.toContain("xl:flex");

        // The replaced nav surfaces are gone entirely.
        expect(wrapper.find("nav[aria-label=Breadcrumb]").exists()).toBe(false);
        expect(wrapper.find("#nav-tabs").exists()).toBe(false);
        expect(wrapper.find("nav[aria-label=Sections]").exists()).toBe(false);
    });

    it("renders one pill per admin section, each with its icon", () => {
        const wrapper = mountAdmin();

        const links = wrapper.findAll("nav[aria-label='Admin sections'] a");
        expect(links.map(l => l.attributes("data-to"))).toEqual([
            "/admin/users",
            "/admin/access-requests",
            "/admin/banner",
            "/admin/deployments"
        ]);
        expect(links.map(l => l.text())).toEqual(["Users", "Access Requests", "Banner", "Deployments"]);
        for (const link of links) {
            expect(link.find("svg").exists()).toBe(true);
        }
    });

    it("marks the active section chip", () => {
        const wrapper = mountAdmin();

        const links = wrapper.findAll("nav[aria-label='Admin sections'] a");
        // Mocked route is /admin/users, so Users is the active chip: a filled
        // primary-tint rounded-full pill; inactive chips are outlined and muted.
        // In dark mode the fill must be the solid brand plum — the primary
        // 800/900 shades are near-black and vanish against the dark surfaces.
        expect(links[0].classes()).toContain("rounded-full");
        expect(links[0].classes()).toContain("bg-primary-200");
        expect(links[0].classes()).toContain("dark:bg-primary-500");
        expect(links[0].classes()).toContain("dark:text-white");
        expect(links[0].classes()).toContain("text-primary-800");
        expect(links[0].attributes("aria-current")).toBe("page");

        expect(links[1].classes()).toContain("rounded-full");
        expect(links[1].classes()).toContain("border-gray-200");
        expect(links[1].classes()).toContain("text-gray-500");
        expect(links[1].classes()).toContain("dark:text-gray-300");
        expect(links[1].attributes("aria-current")).toBeUndefined();
    });
});

describe("admin content wrapper", () => {
    // Gutters moved into the subpages so Users can run edge-to-edge; the wrapper
    // owns the vertical scroll at every breakpoint (the models-page scroll model).
    it("owns the scroll and carries no gutters", () => {
        const wrapper = mountAdmin();

        const content = wrapper.find("[data-test='admin-content']");
        expect(content.exists()).toBe(true);
        expect(content.classes()).toContain("overflow-y-auto");
        expect(content.classes()).toContain("flex-1");
        expect(content.classes()).toContain("min-w-0");
        for (const cls of ["px-4", "py-4", "md:px-8", "md:py-8", "mx-auto", "xl:overflow-y-auto", "xl:overflow-hidden"]) {
            expect(content.classes()).not.toContain(cls);
        }
    });
});
