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

import AddUser from "../ProjectUsers.vue";
import { AddProjectUsers } from "../selectors";

describe("Project Users Modal", () => {
    let component: ReturnType<typeof mount>;

    beforeEach(() => {
        component = mount(AddUser, {
            props: { users: [] },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
    });

    it("Renders the component", () => {
        expect(component.exists()).toBe(true);
    });

    it.each([
        ["infoAlert", true],
        ["optionalText", true],
        ["addUserInput", true],
        ["addUserButton", true],
        ["invalidUsersList", false],
        ["addedUsersList", false]
    ] as const)("%s is rendered initially: %s", (key, present) => {
        expect(component.find(AddProjectUsers[key]).exists()).toBe(present);
    });
});
