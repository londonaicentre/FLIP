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
import { vi } from "vitest";

import { routeChange } from "@/router";

import AiUserDropdown from "../AiUserDropdown.vue";

vi.mock("@/router", () => ({
    routeChange: { changePassword: vi.fn() },
    default: { push: vi.fn() }
}));

describe("Ai UserDropdown", () => {
    it("Renders Component", () => {
        const comp = mount(AiUserDropdown, {
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        expect(comp.exists()).toBe(true);
    });

    it("renders the account menu trigger as a native button for assistive tech", () => {
        const comp = mount(AiUserDropdown, {
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        const trigger = comp.find("[data-test='account-menu-btn']");
        expect(trigger.exists()).toBe(true);
        // aria-haspopup/aria-expanded are only valid on an element with a
        // button role — a plain div invalidates them (Lighthouse aria-allowed-attr).
        expect(trigger.element.tagName).toBe("BUTTON");
    });

    it("keeps a visible keyboard focus ring on the trigger button", () => {
        const comp = mount(AiUserDropdown, {
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // Focus lands on the native button, so the ring utilities must live
        // there — focus:* classes on the inner div never fire.
        const trigger = comp.find("[data-test='account-menu-btn']");
        expect(trigger.classes().some(c => c.startsWith("focus-visible:ring"))).toBe(true);
    });

    it("offers Light Mode with the sun icon while dark mode is active", async () => {
        const comp = mount(AiUserDropdown, {
            props: { isDark: true },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        await comp.find("[data-test='account-menu-btn']").trigger("click");

        const themeButton = comp.findAll("[data-test='sign-out-btn']")[0];
        expect(themeButton.text()).toContain("Light Mode");
        expect(themeButton.find("svg").exists()).toBe(true);
    });

    it("drives the theme toggle, change-password and sign-out actions from the open menu", async () => {
        const comp = mount(AiUserDropdown, {
            props: {
                isDark: false,
                emailAddress: "ada@example.com"
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
        // headlessui closes the menu after each MenuItem click, so re-open per action.
        const openMenu = () => comp.find("[data-test='account-menu-btn']").trigger("click");

        // The theme toggle is the first of the two buttons sharing the sign-out data-test.
        await openMenu();
        await comp.findAll("[data-test='sign-out-btn']")[0].trigger("click");
        expect(comp.emitted("toggleDarkMode")).toHaveLength(1);

        await openMenu();
        await comp.find("[data-test='change-password-btn']").trigger("click");
        expect(routeChange.changePassword).toHaveBeenCalledWith("ada@example.com");

        await openMenu();
        await comp.findAll("[data-test='sign-out-btn']")[1].trigger("click");
        expect(comp.emitted("signOut")).toHaveLength(1);
    });
});
