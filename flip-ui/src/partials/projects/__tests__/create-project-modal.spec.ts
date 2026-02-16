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

import NewProject from "../CreateProjectModal.vue";
import { CreateProjectModal } from "../selectors";

describe("Create Project Modal", () => {
    let component: any;

    beforeEach(() => {
        component = mount(NewProject, {
            props: { open: true }, // Set modal as open
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })],
                stubs: {
                    // Stub Headless UI components to render their slots
                    TransitionRoot: {
                        template: '<div><slot /></div>'
                    },
                    Dialog: {
                        template: '<div><slot /></div>'
                    },
                    DialogPanel: {
                        template: '<div><slot /></div>'
                    },
                    DialogTitle: {
                        template: '<div><slot /></div>'
                    },
                    TransitionChild: {
                        template: '<div><slot /></div>'
                    },
                    // Stub Form component from vee-validate
                    Form: {
                        template: '<form><slot /></form>'
                    },
                    // Stub icons
                    'icon-mdi-close': {
                        template: '<span>×</span>'
                    },
                    // Stub ProjectUsers component
                    'ProjectUsers': {
                        template: '<div>Project Users Component</div>'
                    }
                }
            }
        });
    });

    it("Renders the component", () => {
        expect(component.exists()).toBe(true);
    });

    it("Project name input exists", async () => {
        console.log("Component HTML:", component.html());
        const nameInput = component.find(CreateProjectModal.projectNameInput);
        console.log("Found input:", nameInput.exists());
        expect(nameInput.exists()).toBe(true);
    });

    it("Project description input exists", async () => {
        const descriptionInput = component.find(
            CreateProjectModal.projectDescription
        );
        console.log("Found description:", descriptionInput.exists());
        expect(descriptionInput.exists()).toBe(true);
    });
});
