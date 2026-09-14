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

import { lookupProjectUser } from "@/services/user-service";

import ProjectUsers from "../ProjectUsers.vue";

vi.mock("@/services/user-service", () => ({ lookupProjectUser: vi.fn() }));

function mountProjectUsers(props: Record<string, unknown> = {}) {
    // Cast: the component declares `users` as required, but the whole
    // point of these tests is to exercise the `withDefaults` fallback
    // when the prop is omitted at runtime — TypeScript can't model that.
    return mount(ProjectUsers, {
        props: props as never,
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    initialState: {
                        auth: {
                            user: {
                                username: "u",
                                userId: "current-user",
                                attributes: {
                                    sub: "s",
                                    email: "u@e.com"
                                },
                                permissions: []
                            }
                        }
                    }
                })
            ],
            directives: { tippy: () => {} },
            stubs: {
                // Renders `text` so the add-user error messages are assertable.
                AiAlert: {
                    props: ["text"],
                    template: "<div>{{ text }}<slot /></div>"
                },
                AiButton: { template: "<button><slot /></button>" },
                AiInput: { template: "<input />" },
                // Declares the submit event so tests can drive the real handler without
                // standing up vee-validate.
                Form: {
                    name: "Form",
                    emits: ["submit"],
                    template: "<form><slot /></form>"
                }
            }
        }
    });
}

describe("ProjectUsers — defensive prop default", () => {
    test("renders the empty-state placeholder when the users prop is omitted", () => {
        const wrapper = mountProjectUsers();
        expect(wrapper.text()).toContain("No Project Users");
    });

    test("renders the empty-state placeholder when users is an empty array", () => {
        const wrapper = mountProjectUsers({ users: [] });
        expect(wrapper.text()).toContain("No Project Users");
    });

    test("lists each user row when users is populated", () => {
        const wrapper = mountProjectUsers({
            users: [
                {
                    id: "u1",
                    email: "alice@e.com",
                    isDisabled: false
                },
                {
                    id: "u2",
                    email: "bob@e.com",
                    isDisabled: false
                }
            ]
        });
        expect(wrapper.text()).toContain("alice@e.com");
        expect(wrapper.text()).toContain("bob@e.com");
    });

    test("filters out the current user from the displayed list", () => {
        // Self-row would let a project owner remove themselves; the
        // `displayUsers` computed drops `currentUserId` to prevent that.
        const wrapper = mountProjectUsers({
            users: [
                {
                    id: "current-user",
                    email: "self@e.com",
                    isDisabled: false
                },
                {
                    id: "u2",
                    email: "bob@e.com",
                    isDisabled: false
                }
            ]
        });
        expect(wrapper.text()).not.toContain("self@e.com");
        expect(wrapper.text()).toContain("bob@e.com");
    });
});

describe("ProjectUsers — add-user submit path", () => {
    beforeEach(() => {
        vi.mocked(lookupProjectUser).mockReset();
    });

    // Drives the component's real submit handler. vee-validate's Form is stubbed, so the submit
    // event is emitted directly with the (values, actions) pair the real Form would supply.
    async function submitEmail(wrapper: ReturnType<typeof mountProjectUsers>, email: string) {
        const resetForm = vi.fn();

        wrapper.findComponent({ name: "Form" }).vm.$emit("submit", { email }, { resetForm });
        await flushPromises();

        return resetForm;
    }

    const errorText = (wrapper: ReturnType<typeof mountProjectUsers>) =>
        wrapper.find("[data-test=\"invalid-user-project-list\"]").text();

    test("adds the resolved user and emits the updated list", async () => {
        const resolved = {
            id: "u9",
            email: "new@e.com",
            isDisabled: false
        };
        vi.mocked(lookupProjectUser).mockResolvedValue(resolved);
        const wrapper = mountProjectUsers({ users: [] });

        const resetForm = await submitEmail(wrapper, "new@e.com");

        expect(lookupProjectUser).toHaveBeenCalledWith("new@e.com");
        expect(wrapper.text()).toContain("new@e.com");
        expect(wrapper.emitted("updatedUsers")?.at(-1)?.[0]).toEqual([resolved]);
        expect(resetForm).toHaveBeenCalled();
    });

    test("rejects a disabled account without adding it", async () => {
        vi.mocked(lookupProjectUser).mockResolvedValue({
            id: "u9",
            email: "off@e.com",
            isDisabled: true
        });
        const wrapper = mountProjectUsers({ users: [] });

        await submitEmail(wrapper, "off@e.com");

        expect(errorText(wrapper)).toContain("off@e.com is disabled");
        expect(wrapper.emitted("updatedUsers")).toBeUndefined();
    });

    test("reports an unknown address as not found", async () => {
        // The hub answers an unregistered address with 404 (FLIP#907).
        vi.mocked(lookupProjectUser).mockRejectedValue({ response: { status: 404 } });
        const wrapper = mountProjectUsers({ users: [] });

        await submitEmail(wrapper, "ghost@e.com");

        expect(errorText(wrapper)).toContain("ghost@e.com cannot be found");
        expect(wrapper.emitted("updatedUsers")).toBeUndefined();
    });

    test("does not call the hub for an address already in the list", async () => {
        const wrapper = mountProjectUsers({
            users: [{
                id: "u1",
                email: "alice@e.com",
                isDisabled: false
            }]
        });

        await submitEmail(wrapper, "alice@e.com");

        expect(lookupProjectUser).not.toHaveBeenCalled();
        expect(errorText(wrapper)).toContain("alice@e.com has already been added");
    });
});
