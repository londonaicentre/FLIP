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

import AiHeader from "../AiHeader.vue";

describe("AiHeader", () => {
    test("Renders the top nav with required props", () => {
        const component = mount(AiHeader, {
            props: { title: "Projects", currentPage: "/projects", isDark: false },
            global: {
                stubs: ["router-link"],
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // The page now owns its own H1, so the header renders only the top nav
        // (no visible title element).
        expect(component.find("[data-test='top-nav']").exists()).toBe(true);
    });
});
