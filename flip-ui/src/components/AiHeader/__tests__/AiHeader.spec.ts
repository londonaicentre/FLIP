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
import { describe, expect, it, test, vi } from "vitest";

import AiHeader from "../AiHeader.vue";

// Local stubs override the global `router-link: true` in test/setup.ts so the
// slot children (logos, nav labels, active indicator, mobile menu items) get
// rendered and we can assert on them.
const stubs = {
    "router-link": {
        props: ["to"],
        template: "<a :data-to='to'><slot /></a>"
    },
    Popover: { template: "<header><slot :open='true' :close='() => undefined' /></header>" },
    PopoverButton: { template: "<button data-test='popover-btn'><slot /></button>" },
    PopoverPanel: { template: "<div data-test='popover-panel'><slot /></div>" },
    TransitionRoot: { template: "<div><slot /></div>" },
    TransitionChild: { template: "<div><slot /></div>" },
    AiPopoverOverlay: { template: "<div data-test='popover-overlay' />" }
};

function mountHeader(options: {
    currentPage?: string;
    permissions?: string[];
    deploymentMode?: boolean;
    isDark?: boolean;
} = {}) {
    const { currentPage = "/projects", permissions = [], deploymentMode = false, isDark = false } = options;

    return mount(AiHeader, {
        props: {
            title: "Projects",
            currentPage,
            isDark
        },
        global: {
            stubs,
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: { user: { permissions } },
                    siteDetails: { deploymentMode }
                }
            })],
            directives: { tippy: () => {} }
        }
    });
}

describe("AiHeader", () => {
    test("Renders the top nav with required props", () => {
        const component = mountHeader();

        // The page now owns its own H1, so the header renders only the top nav
        // (no visible title element).
        expect(component.find("[data-test='top-nav']").exists()).toBe(true);
    });

    it("renders the brand logos inside the home link", () => {
        const component = mountHeader();
        const logos = component.findAll("img");

        expect(logos.map(i => i.attributes("alt"))).toEqual(
            expect.arrayContaining(["London AI Centre", "FLIP"])
        );
    });

    it("uses the light AI Centre logo in light mode", () => {
        const component = mountHeader({ isDark: false });

        expect(component.find("img[src=\"/images/aicentre-logo-transparent.webp\"]").exists()).toBe(true);
        expect(component.find("img[src=\"/images/aicentre-logo-transparent-dark.webp\"]").exists()).toBe(false);
    });

    it("uses the dark AI Centre logo in dark mode", () => {
        const component = mountHeader({ isDark: true });

        expect(component.find("img[src=\"/images/aicentre-logo-transparent-dark.webp\"]").exists()).toBe(true);
        expect(component.find("img[src=\"/images/aicentre-logo-transparent.webp\"]").exists()).toBe(false);
    });

    it("names the icon-only mobile menu button for assistive tech", () => {
        const component = mountHeader();

        expect(component.find("[data-test='mobile-menu-btn']").attributes("aria-label")).toBe("Open menu");
    });

    it("uses the light FLIP text logo in light mode", () => {
        const component = mountHeader({ isDark: false });

        expect(component.find("img[src=\"/images/flip-logo-text.webp\"]").exists()).toBe(true);
        expect(component.find("img[src=\"/images/flip-logo-text-dark.webp\"]").exists()).toBe(false);
    });

    it("uses the dark FLIP text logo in dark mode", () => {
        const component = mountHeader({ isDark: true });

        expect(component.find("img[src=\"/images/flip-logo-text-dark.webp\"]").exists()).toBe(true);
        expect(component.find("img[src=\"/images/flip-logo-text.webp\"]").exists()).toBe(false);
    });

    it("renders an entry per navigation item with the matching item marked current", () => {
        const component = mountHeader({
            currentPage: "/connectionstatus",
            permissions: ["CanAccessAdminPanel"]
        });
        const navLinks = component.find("[data-test='top-nav']").findAll("a");

        expect(navLinks.map(l => l.text())).toEqual([
            "Projects",
            "Connection Status",
            "Admin"
        ]);
        // Active item gets the underline indicator span; siblings don't.
        const currentLink = navLinks.find(l => l.text() === "Connection Status")!;
        expect(currentLink.find("span[aria-hidden='true']").exists()).toBe(true);
    });

    it("hides the Admin nav item when the user lacks CanAccessAdminPanel", () => {
        const component = mountHeader({ permissions: [] });
        const navLinks = component.find("[data-test='top-nav']").findAll("a");

        expect(navLinks.map(l => l.text())).not.toContain("Admin");
    });

    it("renders the deployment-mode pulse badge when the site is in deployment mode", () => {
        const component = mountHeader({ deploymentMode: true });

        expect(component.find("[data-test='deployment-mode-status']").exists()).toBe(true);
    });

    it("omits the deployment-mode badge when not in deployment mode", () => {
        const component = mountHeader({ deploymentMode: false });

        expect(component.find("[data-test='deployment-mode-status']").exists()).toBe(false);
    });
});
