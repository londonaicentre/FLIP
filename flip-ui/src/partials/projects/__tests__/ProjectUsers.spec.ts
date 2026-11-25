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

const owner = {
    id: "owner-1",
    email: "owner@e.com",
    isDisabled: false
};

function mountProjectUsers(props: Record<string, unknown> = {}) {
    // Cast: some tests omit `users` to exercise the `withDefaults` fallback,
    // which TypeScript can't model.
    return mount(ProjectUsers, {
        props: {
            ownerId: owner.id,
            ownerEmail: owner.email,
            ...props
        } as never,
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

describe("ProjectUsers — the list", () => {
    const ownerRow = (wrapper: ReturnType<typeof mountProjectUsers>) => wrapper.find("[data-test=project-owner-row]");

    test("lists the owner alone when the users prop is omitted", () => {
        const wrapper = mountProjectUsers();

        expect(ownerRow(wrapper).text()).toContain("owner@e.com");
        expect(ownerRow(wrapper).text()).toContain("Owner");
        expect(wrapper.findAll("[data-test=added-user-project-list]")).toHaveLength(0);
    });

    test("heads the list Project Users", () => {
        expect(mountProjectUsers().find("[data-test=project-users-heading]").text()).toBe("Project Users");
    });

    test("shows the owner once, in their own row, when users includes them", () => {
        // An existing project's `users` holds the owner (the hub adds them).
        const wrapper = mountProjectUsers({
            users: [owner, {
                id: "u2",
                email: "bob@e.com",
                isDisabled: false
            }]
        });

        const members = wrapper.findAll("[data-test=added-user-project-list]");
        expect(members).toHaveLength(1);
        expect(members[0].text()).toContain("bob@e.com");
        expect(ownerRow(wrapper).text()).toContain("owner@e.com");
    });

    test("never offers to remove the owner", () => {
        const wrapper = mountProjectUsers({ users: [owner] });

        expect(ownerRow(wrapper).find("button").exists()).toBe(false);
        expect(wrapper.find("[data-test=remove-user-0-project-btn]").exists()).toBe(false);
    });

    test("lists the signed-in user like any other member", () => {
        // The pinia user is "current-user"; the list no longer hides whoever is viewing.
        const wrapper = mountProjectUsers({
            users: [owner, {
                id: "current-user",
                email: "self@e.com",
                isDisabled: false
            }]
        });

        expect(wrapper.text()).toContain("self@e.com");
        expect(wrapper.find("[data-test=remove-user-0-project-btn]").exists()).toBe(true);
    });

    test("keeps the owner in the emitted list when another member is removed", async () => {
        // Editing replaces the project's whole access list, so dropping the owner here would lock them out.
        const bob = {
            id: "u2",
            email: "bob@e.com",
            isDisabled: false
        };
        const wrapper = mountProjectUsers({ users: [owner, bob] });

        await wrapper.find("[data-test=remove-user-0-project-btn]").trigger("click");

        expect(wrapper.emitted("updatedUsers")?.at(-1)?.[0]).toEqual([owner]);
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

    test("does not call the hub for the owner's own address", async () => {
        const wrapper = mountProjectUsers({ users: [] });

        await submitEmail(wrapper, "owner@e.com");

        expect(lookupProjectUser).not.toHaveBeenCalled();
        expect(errorText(wrapper)).toContain("owner@e.com has already been added");
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

describe("ProjectUsers — errors and removal", () => {
    const alice = {
        id: "u1",
        email: "alice@e.com",
        isDisabled: false
    };

    test("reports a lookup failure other than not-found generically, then clears the message after 5 s", async () => {
        vi.useFakeTimers();
        vi.mocked(lookupProjectUser).mockRejectedValue(new Error("Network Error"));
        const wrapper = mountProjectUsers({ users: [] });

        wrapper.findComponent({ name: "Form" }).vm.$emit("submit", { email: "new@e.com" }, { resetForm: vi.fn() });
        await flushPromises();
        expect(wrapper.text()).toContain("Something went wrong when retrieving the user");

        await vi.advanceTimersByTimeAsync(5_000);
        expect(wrapper.find("[data-test=\"invalid-user-project-list\"]").exists()).toBe(false);
        vi.useRealTimers();
    });

    test("removes a user from the list and emits the remaining users", async () => {
        const wrapper = mountProjectUsers({
            users: [alice, {
                id: "u2",
                email: "bob@e.com",
                isDisabled: false
            }]
        });

        await wrapper.find("[data-test=remove-user-1-project-btn]").trigger("click");

        expect(wrapper.text()).not.toContain("bob@e.com");
        expect(wrapper.emitted("updatedUsers")?.at(-1)?.[0]).toEqual([alice]);
    });

    test("lists the users without add or remove controls when read-only", () => {
        const wrapper = mountProjectUsers({
            users: [alice],
            readonly: true
        });

        expect(wrapper.text()).toContain("Project Users");
        expect(wrapper.text()).toContain("alice@e.com");
        expect(wrapper.find("form").exists()).toBe(false);
        expect(wrapper.find("[data-test=remove-user-0-project-btn]").exists()).toBe(false);
    });
});
