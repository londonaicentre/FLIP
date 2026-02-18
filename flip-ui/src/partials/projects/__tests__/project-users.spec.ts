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
    let component: any;
    
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

    it("Alert exists regarding adding users", async () => {
        const infoAlert = component.find(AddProjectUsers.infoAlert);
        expect(infoAlert.exists()).toBeTruthy();
    });

    it("Displays the 'Optional' text", async () => {
        const optionalText = component.find(AddProjectUsers.optionalText);
        expect(optionalText.exists()).toBeTruthy();
    });

    it("Add user input exists", async () => {
        const addUserInput = component.find(AddProjectUsers.addUserInput);
        expect(addUserInput.exists()).toBeTruthy();
    });

    it("Add user button exists", async () => {
        const addUserButton = component.find(AddProjectUsers.addUserButton);
        expect(addUserButton.exists()).toBeTruthy();
    });

    it("Does not initially display the element containing the list of invalid users", async () => {
        const invalidUsersList = component.find(AddProjectUsers.invalidUsersList);
        expect(invalidUsersList.exists()).toBeFalsy();
    });

    it("Does not initially display the element containing the the list of users added to the project", async () => {
        const addedUsersList = component.find(AddProjectUsers.addedUsersList);
        expect(addedUsersList.exists()).toBeFalsy();
    });
});
