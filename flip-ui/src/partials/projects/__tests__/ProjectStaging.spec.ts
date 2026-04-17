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

import ProjectStaging from "@/partials/projects/ProjectStaging.vue";

describe("ProjectStaging", () => {
    it("emits staged when selected trusts are provided", async () => {
        const component = mount(ProjectStaging, {
            props: {
                staging: false,
                hasQuery: true
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                permissions: ["CanManageProjects"]
                            }
                        },
                        trust: {
                            trusts: [
                                {
                                    id: "trust-1",
                                    name: "Trust One"
                                }
                            ]
                        }
                    }
                })]
            }
        });

        const form = component.findComponent({ name: "Form" });
        form.vm.$emit("submit", { trusts: ["trust-1"] });
        await component.vm.$nextTick();

        expect(component.emitted("staged")).toEqual([[ ["trust-1"] ]]);
    });
});
