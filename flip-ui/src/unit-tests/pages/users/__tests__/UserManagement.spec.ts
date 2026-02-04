/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

import UserManagement from "@/pages/admin/users.vue";

describe("User Management", () => {
    it("renders the component successfully", () => {
        const component = mount(UserManagement, { 
            global: { 
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            } 
        });

        expect(component.exists()).toBe(true);
    });
});
