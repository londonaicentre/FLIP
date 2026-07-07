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
import { describe, expect, test, vi } from "vitest";

import AiMainNavigation from "../AiMainNavigation.vue";

const LIGHT_LOGO = "/images/aicentre-logo-transparent.webp";
const DARK_LOGO = "/images/aicentre-logo-transparent-dark.webp";

function mountNav(isDark: boolean) {
    return mount(AiMainNavigation, {
        global: {
            // Render router-link slots so the logo inside the home link is asserted on.
            stubs: { "router-link": { template: "<a><slot /></a>" } },
            directives: { tippy: () => {} },
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })]
        },
        props: {
            currentPage: "/project",
            isDark
        }
    });
}

describe("Ai MainNavigation", () => {
    test("Renders Component", () => {
        expect(mountNav(false).exists()).toBe(true);
    });

    test("shows the light AI Centre logo in light mode", () => {
        const comp = mountNav(false);

        expect(comp.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(true);
        expect(comp.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(false);
    });

    test("shows the dark AI Centre logo in dark mode", () => {
        const comp = mountNav(true);

        expect(comp.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(true);
        expect(comp.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(false);
    });
});
