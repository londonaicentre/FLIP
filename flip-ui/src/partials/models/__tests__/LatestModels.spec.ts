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
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ref } from "vue";

import LatestModels from "../LatestModels.vue";

// Stub vue-router so the component's `useRoute` returns a deterministic
// projectId — without it, the SWRV key function evaluates to "" and the
// fetcher never runs.
const mockRoute = { params: { projectId: "project-1" } as Record<string, string> };
vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

// SWRV is the data source. We swap it for a controllable ref so each test
// can drive it through the loading / empty-payload / populated states the
// real `getModels` would hand back. The optional-chain fixes in the
// component (`data?.data?.length`, `data?.data ?? []`) are exactly what
// these tests guard. Using vi.hoisted + a real Vue ref so that the
// component's `data?.data?.length` and v-for see reactive updates.
const mockData = vi.hoisted(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const vue = require("vue") as typeof import("vue");

    return { ref: vue.ref<unknown>(undefined) };
});
vi.mock("swrv", () => ({
    default: () => ({
        data: mockData.ref,
        error: ref(null)
    })
}));

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        getModels: vi.fn(async () => undefined)
    };
});

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

function setData(v: unknown) {
    (mockData.ref as { value: unknown }).value = v;
}

interface MountOptions {
    isViewer?: boolean;
    projectStatus?: string;
}

function mountLatestModels({
    isViewer = false,
    projectStatus = "APPROVED"
}: MountOptions = {}) {
    return mount(LatestModels, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    // stubActions: false runs the real action implementations
                    // against the initialState — needed so authStore.hasPermissions
                    // reflects the seeded `user.permissions` array instead of a
                    // spy that returns undefined and makes isViewer always true.
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "u",
                                userId: "id",
                                attributes: {
                                    sub: "s",
                                    email: "u@e.com"
                                },
                                permissions: isViewer ? [] : ["CanCreateProjects"]
                            },
                            signInStep: "DONE",
                            mfaEnabled: true,
                            mfaRequired: true
                        },
                        project: {
                            project: {
                                id: "project-1",
                                name: "Test",
                                status: projectStatus
                            }
                        }
                    }
                })
            ],
            stubs: {
                AiCard: { template: "<div><slot /></div>" },
                AiButton: {
                    // Forward DOM clicks as Vue `click` emits so the parent's
                    // `@click="addModel"` listener fires when the test
                    // triggers `.trigger("click")` on this stub. All attrs
                    // (data-test, aria-label) are forwarded onto the button.
                    template: "<button v-bind=\"$attrs\" @click=\"$emit('click', $event)\"><slot /></button>",
                    inheritAttrs: false,
                    emits: ["click"]
                },
                AiAlert: { template: "<div><slot /></div>" },
                AiLoader: { template: "<div data-test='ai-loader' />" },
                "router-link": {
                    template: "<a><slot :navigate='() => {}' /></a>",
                    props: ["to"]
                }
            }
        }
    });
}

describe("LatestModels — defensive data access", () => {
    beforeEach(() => {
        setData(undefined);
    });

    test("renders the loader while SWRV data is undefined", async () => {
        setData(undefined);
        const wrapper = mountLatestModels();
        await flushPromises();

        expect(wrapper.find("[data-test=ai-loader]").exists()).toBe(true);
    });

    test("does not throw when SWRV resolves to an empty {} (unstubbed catch-all path)", async () => {
        // Cypress's globalIntercepts catch-all returns `{ statusCode: 200 }`
        // for any /projects/<id>/models call that isn't explicitly stubbed.
        // Axios resolves that as `data: ""` or `{}` depending on parsing,
        // both of which leave `data.data` undefined. The component's
        // `data?.data?.length` chain has to short-circuit before reaching
        // the unsafe `.length` access — without it, an unstubbed response
        // surfaces as an unhandled rejection at first paint.
        setData({});
        const wrapper = mountLatestModels();
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
    });

    test("renders the empty-state alert when data.data is an empty array", async () => {
        setData({ data: [] });
        const wrapper = mountLatestModels();
        await flushPromises();

        expect(wrapper.text()).toContain("No models have been created for this project yet.");
    });

    test("keeps the approval-required alert under the title instead of vertically centring it", async () => {
        const wrapper = mountLatestModels({ projectStatus: "UNSTAGED" });
        await flushPromises();

        const alert = wrapper.find("[data-test=approval-required-alert]");
        expect(alert.exists()).toBe(true);
        // m-auto would centre the alert in the pinned-height flex-column card.
        expect(alert.classes()).not.toContain("m-auto");
        expect(alert.classes()).toContain("shrink-0");
    });

    test("centres the borderless empty state vertically in the card", async () => {
        setData({ data: [] });
        const wrapper = mountLatestModels();
        await flushPromises();

        const empty = wrapper.find("[data-test=models-empty-state]");
        for (const cls of ["flex-1", "items-center", "justify-center"]) {
            expect(empty.classes()).toContain(cls);
        }
        // No framed box around the icon + copy any more.
        expect(empty.html()).not.toContain("border-2");
    });

    test("shows the status chip on the same row as the model name", async () => {
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: "",
                status: "TRAINING_STARTED"
            }]
        });
        const wrapper = mountLatestModels();
        await flushPromises();

        const chip = wrapper.find("[data-test='latest-model-status-chip']");
        expect(chip.classes()).toContain("rounded-full");
        expect(chip.classes().join(" ")).toContain("bg-fuchsia-100");
        expect(chip.text()).toBe("Training Started");
        // Same row as the name: they share a flex parent.
        const nameRow = chip.element.parentElement;
        expect(nameRow?.textContent).toContain("Alpha");
    });

    test("lists models and shows the View All button when data.data is populated", async () => {
        setData({
            data: [
                {
                    id: "m1",
                    name: "Alpha",
                    description: ""
                },
                {
                    id: "m2",
                    name: "Beta",
                    description: "second model"
                }
            ]
        });
        const wrapper = mountLatestModels();
        await flushPromises();

        expect(wrapper.text()).toContain("Alpha");
        expect(wrapper.text()).toContain("Beta");
        // The View-All button only renders when data.data.length > 0.
        expect(wrapper.text()).toContain("View All Models");
    });

    test("lays out as a flex column whose list region scrolls when the card height is pinned", async () => {
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: ""
            }]
        });
        const wrapper = mountLatestModels();
        await flushPromises();

        // The project page pins this card to the imaging-status row height; the
        // root must be a flex column so the list flexes and scrolls internally.
        expect(wrapper.classes()).toContain("flex");
        expect(wrapper.classes()).toContain("flex-col");
        const list = wrapper.find("[data-test=models-approved-status]");
        for (const cls of ["flex-1", "min-h-0", "overflow-y-auto"]) {
            expect(list.classes()).toContain(cls);
        }
    });

    test("shows the header Create-Model button when not a viewer", async () => {
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: ""
            }]
        });
        const wrapper = mountLatestModels({ isViewer: false });
        await flushPromises();

        expect(wrapper.find("[data-test=add-model-btn]").exists()).toBe(true);

        // Clicking exercises addModel() in the script setup.
        await wrapper.find("[data-test=add-model-btn]").trigger("click");
        await flushPromises();
        expect(wrapper.exists()).toBe(true);
    });

    test("shows the header Create-Model button for a Researcher (CanCreateProjects only)", async () => {
        // Researchers don't have CanManageProjects — per-project access is
        // enforced server-side. The UI gate must not exclude them.
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: ""
            }]
        });
        const wrapper = mountLatestModels({ isViewer: false });
        await flushPromises();

        expect(wrapper.find("[data-test=add-model-btn]").exists()).toBe(true);
    });

    test("hides the header Create-Model button for viewers", async () => {
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: ""
            }]
        });
        const wrapper = mountLatestModels({ isViewer: true });
        await flushPromises();

        expect(wrapper.find("[data-test=add-model-btn]").exists()).toBe(false);
    });

    test("hides the header Create-Model button for viewers in the empty state", async () => {
        setData({ data: [] });
        const wrapper = mountLatestModels({ isViewer: true });
        await flushPromises();

        expect(wrapper.find("[data-test=add-model-btn]").exists()).toBe(false);
    });

    test("non-viewer empty state renders the create-model CTA in the header", async () => {
        setData({ data: [] });
        const wrapper = mountLatestModels({ isViewer: false });
        await flushPromises();

        expect(wrapper.find("[data-test=add-model-btn]").exists()).toBe(true);

        await wrapper.find("[data-test=add-model-btn]").trigger("click");
        expect(wrapper.exists()).toBe(true);
    });

    test("Create-Model button collapses its label below lg with an aria-label and an icon", async () => {
        setData({ data: [] });
        const wrapper = mountLatestModels({ isViewer: false });
        await flushPromises();

        const btn = wrapper.find("[data-test=add-model-btn]");
        expect(btn.exists()).toBe(true);
        expect(btn.attributes("aria-label")).toBe("Create Model");
        expect(btn.find("svg").exists()).toBe(true);
        const label = btn.find("span.hidden.lg\\:inline");
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("Create Model");
    });

    test("does not throw when project status is non-APPROVED and data.data is undefined", async () => {
        // Project hasn't been approved yet — the bulk of the conditional
        // template renders the unapproved-status placeholder. The guards
        // matter because the `data?.data?.length` is still evaluated for
        // the Create-Model header button even though the project list
        // body is hidden.
        setData({});
        const wrapper = mount(LatestModels, {
            global: {
                plugins: [
                    createTestingPinia({
                        createSpy: vi.fn,
                        initialState: {
                            auth: {
                                user: {
                                    username: "u",
                                    userId: "id",
                                    attributes: {},
                                    permissions: ["CanCreateProjects"]
                                },
                                signInStep: "DONE",
                                mfaEnabled: true
                            },
                            project: {
                                project: {
                                    id: "project-1",
                                    name: "Test",
                                    status: "UNSTAGED"
                                }
                            }
                        }
                    })
                ],
                stubs: {
                    AiCard: { template: "<div><slot /></div>" },
                    AiButton: { template: "<button><slot /></button>" },
                    AiAlert: { template: "<div><slot /></div>" },
                    AiLoader: { template: "<div data-test='ai-loader' />" },
                    "router-link": {
                        template: "<a><slot /></a>",
                        props: ["to"]
                    }
                }
            }
        });
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
    });
});
