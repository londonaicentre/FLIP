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
import { beforeEach, describe, expect, it, vi } from "vitest";

import QueryDetails from "@/partials/cohort-query/QueryDetails.vue";
import { IProjectQuery } from "@/services/project-service";

const routerPush = vi.fn();

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRouter: () => ({ push: routerPush })
    };
});

const baseQuery = (): IProjectQuery => ({
    id: "q-1",
    name: "q",
    query: "SELECT 1",
    queriedTrustIds: ["t1"],
    pendingTrustIds: [],
    cancelledTrustIds: [],
    respondedTrustIds: ["t1"],
    erroredTrustIds: [],
    emptyTrustIds: [],
    totalCohort: 100
});

function mountQueryDetails(queryDetails?: IProjectQuery) {
    return mount(QueryDetails, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        project: {
                            project: {
                                id: "p-1",
                                name: "P"
                            }
                        },
                        auth: {
                            user: {
                                username: "u",
                                userId: "id",
                                attributes: {
                                    sub: "s",
                                    email: "u@e.com"
                                },
                                permissions: ["CanCreateProjects"]
                            },
                            signInStep: "DONE"
                        }
                    }
                })
            ],
            stubs: {
                AiCard: { template: "<div><slot /></div>" },
                AiButton: {
                    inheritAttrs: false,
                    template: "<button v-bind=\"$attrs\" @click=\"$emit('click', $event)\"><slot /></button>",
                    emits: ["click"]
                }
            }
        },
        props: { queryDetails }
    });
}

beforeEach(() => {
    routerPush.mockReset();
});

describe("QueryDetails View Query button", () => {
    // Same collapse treatment as the page-header actions: below lg the label
    // hides leaving the icon, with an aria-label keeping the button named.
    it("collapses its label below lg with an aria-label and an eye icon", () => {
        const wrapper = mountQueryDetails(baseQuery());

        const btn = wrapper.find("[data-test=view-results-btn]");
        expect(btn.exists()).toBe(true);
        expect(btn.attributes("aria-label")).toBe("View Query");
        expect(btn.find("svg").exists()).toBe(true);
        const label = btn.find("span.hidden.lg\\:inline");
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("View Query");
    });

    it("navigates to the cohort query page on click", async () => {
        const wrapper = mountQueryDetails(baseQuery());

        await wrapper.find("[data-test=view-results-btn]").trigger("click");

        expect(routerPush).toHaveBeenCalledWith({ path: "/project/p-1/cohort-query" });
    });
});
