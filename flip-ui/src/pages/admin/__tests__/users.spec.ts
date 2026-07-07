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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

import { IUser } from "@/services/user-service";

import UsersPage from "../users.vue";

interface IUserPage {
    data: IUser[];
    totalPages: number;
    page: number;
    pageSize: number;
    totalRecords: number;
}

interface IRolesPage {
    roles: { id: string; rolename: string; roledescription: string }[];
}

const userPage = ref<IUserPage | undefined>(undefined);
const rolesPage = ref<IRolesPage | undefined>(undefined);
const mutateUsers = vi.fn();

vi.mock("swrv", () => ({
    default: (keyFn: () => string) => {
        const key = typeof keyFn === "function" ? keyFn() : keyFn;
        // First call requests /users…, second /roles. Route by key prefix.
        if (key.startsWith("/users")) {
            return {
                data: userPage,
                mutate: mutateUsers,
                error: ref(null)
            };
        }

        return {
            data: rolesPage,
            mutate: vi.fn(),
            error: ref(null)
        };
    }
}));

const mockUpdateUserProfile = vi.fn();
const mockUpdateUserRoles = vi.fn();
const mockUpdateUserDisabledState = vi.fn();
const mockResetUserMfa = vi.fn();

vi.mock("@/services/user-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/user-service")>();

    return {
        ...actual,
        updateUserProfile: (...args: unknown[]) => mockUpdateUserProfile(...args),
        updateUserRoles: (...args: unknown[]) => mockUpdateUserRoles(...args),
        updateUserDisabledState: (...args: unknown[]) => mockUpdateUserDisabledState(...args),
        resetUserMfa: (...args: unknown[]) => mockResetUserMfa(...args)
    };
});

const mockSnackbarSuccess = vi.fn();
const mockSnackbarError = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: (...args: unknown[]) => mockSnackbarSuccess(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args),
        show: vi.fn(),
        warning: vi.fn()
    }
}));

const mockRouteChangeViewProjects = vi.fn();

vi.mock("@/router", () => ({
    routeChange: { viewProjects: (...args: unknown[]) => mockRouteChangeViewProjects(...args) },
    default: { push: vi.fn() }
}));

vi.mock("@/utils/route-validator", () => ({ canAccessRoute: vi.fn().mockResolvedValue(true) }));

const stubs = {
    AiButton: {
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" :disabled='disabled' @click=\"$emit('click', $event)\"><slot /></button>",
        props: ["disabled"]
    },
    AiSkeleton: { template: "<div data-test='skeleton' />" },
    AiPagination: { template: "<div />" },
    AiSearch: {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: "<input data-test='user-search' :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)' />"
    },
    AiConfirmModal: {
        name: "AiConfirmModal",
        props: ["dialog", "continueAction", "confirmationText"],
        emits: ["close-modal"],
        // Surface the confirmation text so tests can distinguish each instance.
        template: "<div data-test='confirm-modal' :data-dialog='dialog' :data-text='confirmationText' />"
    },
    RegisterUserModal: {
        name: "RegisterUserModal",
        props: ["dialog"],
        emits: ["close-modal", "on-success"],
        template: "<div data-test='register-user-modal' :data-dialog='dialog' />"
    },
    RoleBadge: { template: "<span data-test='role-badge'><slot /></span>" },
    UserAvatar: { template: "<div data-test='user-avatar' />" }
};

const USERS: IUser[] = [
    {
        id: "u1",
        email: "alice@example.com",
        name: "Alice",
        organisation: "Org A",
        roles: [{
            id: "r-admin",
            rolename: "Admin",
            roledescription: "Admin role"
        }],
        isDisabled: false
    },
    {
        id: "u2",
        email: "bob@example.com",
        name: "Bob",
        organisation: "Org B",
        roles: [{
            id: "r-data",
            rolename: "Data Scientist",
            roledescription: "Researcher"
        }],
        isDisabled: true
    }
];

const ROLES = [
    {
        id: "r-admin",
        rolename: "Admin",
        roledescription: "Admin role"
    },
    {
        id: "r-data",
        rolename: "Data Scientist",
        roledescription: "Researcher"
    }
];

function mountPage(options: { users?: IUser[]; roles?: typeof ROLES; totalRecords?: number } = {}) {
    const { users = USERS, roles = ROLES, totalRecords = users.length } = options;
    userPage.value = {
        data: users,
        totalPages: 1,
        page: 1,
        pageSize: 20,
        totalRecords
    };
    rolesPage.value = { roles };

    return mount(UsersPage, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: { auth: { user: { permissions: ["CanManageUsers"] } } }
            })],
            stubs
        }
    });
}

const findConfirmModal = (wrapper: ReturnType<typeof mountPage>, contains: string) =>
    wrapper.findAll("[data-test='confirm-modal']").find(m => m.attributes("data-text")?.includes(contains));

beforeEach(() => {
    userPage.value = undefined;
    rolesPage.value = undefined;
    mutateUsers.mockReset();
    mockUpdateUserProfile.mockReset();
    mockUpdateUserRoles.mockReset();
    mockUpdateUserDisabledState.mockReset();
    mockResetUserMfa.mockReset();
    mockSnackbarSuccess.mockReset();
    mockSnackbarError.mockReset();
    mockRouteChangeViewProjects.mockReset();
});

afterEach(() => {
    vi.clearAllMocks();
});

describe("admin/users", () => {
    it("renders a row per user once data lands", async () => {
        const wrapper = mountPage();
        await nextTick();
        expect(wrapper.findAll("[data-test='user']")).toHaveLength(2);
    });

    it("selecting a user reveals the editor pane", async () => {
        const wrapper = mountPage();
        await nextTick();
        // Empty-state placeholder is gone after click.
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        expect(wrapper.find("[data-test='selected-user-name-field']").exists()).toBe(true);
        expect(wrapper.find("[data-test='selected-user-email']").text()).toBe("alice@example.com");
    });

    it("filters the user list by search query (case-insensitive substring)", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.find("[data-test='user-search']").setValue("bob");
        expect(wrapper.findAll("[data-test='user']")).toHaveLength(1);
        expect(wrapper.text()).toContain("Bob");
    });

    it("countSummary changes shape between paginated, searched, and empty pages", async () => {
        const wrapper = mountPage({ totalRecords: 42 });
        await nextTick();
        expect(wrapper.find("[data-test='user-count']").text()).toBe("1–2 of 42");

        await wrapper.find("[data-test='user-search']").setValue("alice");
        await nextTick();
        expect(wrapper.find("[data-test='user-count']").text()).toBe("1 of 2 on this page match");

        // Empty page → "0 of <total>".
        await wrapper.find("[data-test='user-search']").setValue("");
        userPage.value = {
            data: [],
            totalPages: 1,
            page: 1,
            pageSize: 20,
            totalRecords: 42
        };
        await nextTick();
        expect(wrapper.find("[data-test='user-count']").text()).toBe("0 of 42");
    });

    it("editing the name flips the dirty flag and unlocks Save User", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const saveBtn = wrapper.find("[data-test='save-user-btn']");
        expect(saveBtn.attributes("disabled")).toBeDefined();

        await wrapper.find("[data-test='selected-user-name-field']").trigger("input");
        await nextTick();
        expect(saveBtn.attributes("disabled")).toBeUndefined();
    });

    it("saveUser calls updateUserProfile only when the profile is dirty", async () => {
        mockUpdateUserProfile.mockResolvedValue({});
        mockUpdateUserRoles.mockResolvedValue([]);
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        await wrapper.find("[data-test='selected-user-name-field']").trigger("input");
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        // The AiButton stub forwards `@click` and the native button bubbles
        // the same event, so the handler may fire more than once — we just
        // care that it fires at all and the right service is called.
        expect(mockUpdateUserProfile).toHaveBeenCalled();
        expect(mockUpdateUserRoles).not.toHaveBeenCalled();
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "User updated" }));
    });

    it("saveUser revalidates the users list so the left rail reflects the edit", async () => {
        mockUpdateUserProfile.mockResolvedValue({});
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        await wrapper.find("[data-test='selected-user-name-field']").trigger("input");
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        // Without a revalidation the SWRV cache keeps serving the stale
        // list until the operator manually reloads the page.
        expect(mutateUsers).toHaveBeenCalled();
    });

    it("does not revalidate the users list when saveUser rejects", async () => {
        mockUpdateUserProfile.mockRejectedValue(new Error("nope"));
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        await wrapper.find("[data-test='selected-user-name-field']").trigger("input");
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        expect(mutateUsers).not.toHaveBeenCalled();
    });

    it("editing the organisation marks the profile dirty and saveUser persists the new value", async () => {
        mockUpdateUserProfile.mockResolvedValue({});
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        await wrapper.find("[data-test='selected-user-organisation-field']").setValue("New Org");
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        expect(mockUpdateUserProfile).toHaveBeenCalledWith(
            "u1",
            expect.objectContaining({ organisation: "New Org" })
        );
    });

    it("Reset Password button opens its confirmation dialog", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const resetModal = () => wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText")?.includes("reset this user's password"))!;
        expect(resetModal().props("dialog")).toBe(false);

        await wrapper.find("[data-test='reset-password-btn']").trigger("click");
        expect(resetModal().props("dialog")).toBe(true);
    });

    it("selectRole marks the user dirty and saveUser persists the new role", async () => {
        mockUpdateUserRoles.mockResolvedValue([]);
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        // Alice starts as Admin — pick the Data Scientist card.
        const dataRoleCard = wrapper.find("[data-test='select-data scientist-role'] input[type='radio']");
        await dataRoleCard.setValue(true);
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        expect(mockUpdateUserRoles).toHaveBeenCalledWith("u1", ["r-data"]);
        expect(mockUpdateUserProfile).not.toHaveBeenCalled();
    });

    it("surfaces an error snackbar when saveUser rejects", async () => {
        mockUpdateUserProfile.mockRejectedValue(new Error("boom"));
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        await wrapper.find("[data-test='selected-user-name-field']").trigger("input");
        await wrapper.find("[data-test='save-user-btn']").trigger("click");
        await flushPromises();

        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Update failed" }));
    });

    it("disable button opens the confirm modal; confirming calls updateUserDisabledState and shows success", async () => {
        mockUpdateUserDisabledState.mockResolvedValue({ disabled: true });
        const wrapper = mountPage();
        await nextTick();
        // Alice (u1) is active, so the Disable button is visible.
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        await wrapper.find("[data-test='disable-user-btn']").trigger("click");

        const modal = findConfirmModal(wrapper, "disable")!;
        expect(modal.attributes("data-dialog")).toBe("true");

        // The continueAction is bound as a prop on the modal — invoke it directly.
        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText") === "Are you sure you want to disable this user?")!;
        await cmp.props("continueAction")();
        await flushPromises();

        expect(mockUpdateUserDisabledState).toHaveBeenCalledWith("u1", { disabled: true });
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "User disabled" }));
    });

    it("enable flow targets the disabled user and shows the enabled snackbar", async () => {
        mockUpdateUserDisabledState.mockResolvedValue({ disabled: false });
        const wrapper = mountPage();
        await nextTick();
        // Bob (u2) is disabled.
        await wrapper.findAll("[data-test='user']")[1].trigger("click");
        await wrapper.find("[data-test='enable-user-btn']").trigger("click");

        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText") === "Are you sure you want to enable this user?")!;
        await cmp.props("continueAction")();
        await flushPromises();

        expect(mockUpdateUserDisabledState).toHaveBeenCalledWith("u2", { disabled: false });
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "User enabled" }));
    });

    it("surfaces an error snackbar when updateUserDisabledState rejects", async () => {
        mockUpdateUserDisabledState.mockRejectedValue(new Error("nope"));
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");
        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText") === "Are you sure you want to disable this user?")!;
        await cmp.props("continueAction")();
        await flushPromises();

        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "User not updated" }));
    });

    it("resetPassword closes its dialog and calls authStore.resetPassword", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText")?.includes("reset this user's password"))!;
        await cmp.props("continueAction")();
        await flushPromises();

        const { useAuthStore } = await import("@/store/auth");
        const authStore = useAuthStore();
        expect(authStore.resetPassword).toHaveBeenCalledWith("alice@example.com");
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "Password reset" }));
    });

    it("resetMfa success shows the success snackbar", async () => {
        mockResetUserMfa.mockResolvedValue(undefined);
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText")?.includes("Reset this user's MFA"))!;
        await cmp.props("continueAction")();
        await flushPromises();

        expect(mockResetUserMfa).toHaveBeenCalledWith("u1");
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "MFA reset" }));
    });

    it("resetMfa failure surfaces the error snackbar", async () => {
        mockResetUserMfa.mockRejectedValue(new Error("kaboom"));
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const cmp = wrapper.findAllComponents({ name: "AiConfirmModal" })
            .find(c => c.props("confirmationText")?.includes("Reset this user's MFA"))!;
        await cmp.props("continueAction")();
        await flushPromises();

        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "MFA reset failed" }));
    });

    it("register-user button opens RegisterUserModal", async () => {
        const wrapper = mountPage();
        await nextTick();
        const modal = wrapper.findComponent({ name: "RegisterUserModal" });
        expect(modal.props("dialog")).toBe(false);
        await wrapper.find("[data-test='register-user-btn']").trigger("click");
        expect(modal.props("dialog")).toBe(true);
    });

    it("RegisterUserModal on-success mutates the user list", async () => {
        mutateUsers.mockResolvedValue(undefined);
        const wrapper = mountPage();
        await nextTick();
        const modal = wrapper.findComponent({ name: "RegisterUserModal" });
        await modal.vm.$emit("on-success");
        await flushPromises();
        expect(mutateUsers).toHaveBeenCalled();
    });
});

describe("shared-squash layout and icon collapse", () => {
    // On lg+ the rail sits beside the editor at 384px, shrinking down to a
    // 240px floor when squashed; below lg it goes full-width because only one
    // pane renders at a time (see the mobile drill-in describe).
    it("lets the user-list rail shrink with the viewport down to a fixed floor", async () => {
        const wrapper = mountPage();
        await nextTick();

        const rail = wrapper.find("[data-test='user-list-rail']");
        expect(rail.exists()).toBe(true);
        expect(rail.classes()).toContain("w-full");
        expect(rail.classes()).toContain("lg:w-96");
        expect(rail.classes()).toContain("shrink");
        expect(rail.classes()).toContain("min-w-[15rem]");
        expect(rail.classes()).toContain("lg:border-r");
        expect(rail.classes()).not.toContain("border-r");
        expect(rail.classes()).not.toContain("flex-shrink-0");
    });

    it("keeps the Save User button labelled with a save icon in the desktop header", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const btn = wrapper.find("[data-test='save-user-btn']");
        expect(btn.attributes("aria-label")).toBe("Save User");
        expect(btn.find("svg").exists()).toBe(true);
        expect(btn.text()).toContain("Save User");
    });

    it("collapses the Register User label below lg with an aria-label", async () => {
        const wrapper = mountPage();
        await nextTick();

        const btn = wrapper.find("[data-test='register-user-btn']");
        expect(btn.attributes("aria-label")).toBe("Register User");
        expect(btn.find("svg").exists()).toBe(true);
        const label = btn.find("span.hidden.lg\\:inline");
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("Register User");
    });
});

describe("mobile drill-in (below lg)", () => {
    // Below lg only one pane shows at a time: the full-width list, or — after
    // tapping a row — the full-screen detail with a back header and a sticky
    // Save bar. Visibility is class-driven (hidden/lg:flex) so at lg+ both
    // panes always render side-by-side exactly as before.
    it("shows the list and hides the detail pane before drilling in", async () => {
        const wrapper = mountPage();
        await nextTick();

        const rail = wrapper.find("[data-test='user-list-rail']");
        const detail = wrapper.find("[data-test='user-detail-pane']");
        expect(rail.classes()).toContain("flex");
        expect(rail.classes()).not.toContain("hidden");
        expect(detail.classes()).toContain("hidden");
        expect(detail.classes()).toContain("lg:flex");
    });

    it("drills into the detail pane when a row is tapped", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const rail = wrapper.find("[data-test='user-list-rail']");
        const detail = wrapper.find("[data-test='user-detail-pane']");
        expect(rail.classes()).toContain("hidden");
        expect(rail.classes()).toContain("lg:flex");
        expect(detail.classes()).toContain("flex");
        expect(detail.classes()).not.toContain("hidden");
    });

    it("returns to the list on back while keeping the selection for desktop", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const backHeader = wrapper.find("[data-test='detail-back-header']");
        expect(backHeader.classes()).toContain("lg:hidden");
        const back = wrapper.find("[data-test='back-to-users-btn']");
        expect(back.attributes("aria-label")).toBe("Back to user list");
        await back.trigger("click");

        const rail = wrapper.find("[data-test='user-list-rail']");
        expect(rail.classes()).toContain("flex");
        expect(rail.classes()).not.toContain("hidden");
        // Selection survives back so a resize to desktop still shows the editor.
        expect(wrapper.find("[data-test='selected-user-name-field']").exists()).toBe(true);
    });

    it("slides the sticky Save bar in only when the record is dirty and saves from it", async () => {
        mockUpdateUserProfile.mockResolvedValue(undefined);
        mutateUsers.mockResolvedValue(undefined);
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const bar = wrapper.find("[data-test='mobile-save-bar']");
        expect(bar.classes()).toContain("fixed");
        expect(bar.classes()).toContain("bottom-0");
        expect(bar.classes()).toContain("lg:hidden");
        expect(bar.classes()).toContain("translate-y-[130%]");

        await wrapper.find("[data-test='selected-user-name-field']").setValue("New Name");
        expect(bar.classes()).toContain("translate-y-0");

        await wrapper.find("[data-test='mobile-save-user-btn']").trigger("click");
        await flushPromises();
        expect(mockUpdateUserProfile).toHaveBeenCalledWith("u1", {
            name: "New Name",
            organisation: "Org A"
        });
        expect(bar.classes()).toContain("translate-y-[130%]");
    });

    it("keeps the header Save button desktop-only and stacks the status pill on mobile", async () => {
        const wrapper = mountPage();
        await nextTick();
        await wrapper.findAll("[data-test='user']")[0].trigger("click");

        const headerSave = wrapper.find("[data-test='save-user-btn']");
        expect(headerSave.classes()).toContain("hidden");
        expect(headerSave.classes()).toContain("lg:block");

        const mobilePill = wrapper.find("[data-test='user-status-mobile']");
        expect(mobilePill.exists()).toBe(true);
        expect(mobilePill.classes()).toContain("lg:hidden");

        const desktopPill = wrapper.find("[data-test='user-status']");
        expect(desktopPill.classes()).toContain("hidden");
        expect(desktopPill.classes()).toContain("lg:inline-flex");
    });

    it("gives list rows a touch-friendly height and a mobile drill-in chevron", async () => {
        const wrapper = mountPage();
        await nextTick();

        const row = wrapper.findAll("[data-test='user']")[0];
        expect(row.classes()).toContain("min-h-[60px]");
        expect(row.find("svg.lg\\:hidden").exists()).toBe(true);
    });
});

describe("de-carded shell", () => {
    // The AiCard chrome (shadow/ring) is gone, but the master–detail keeps the
    // same page margins as the Projects/Models views: a gutter wrapper frames a
    // bordered rounded box that owns the overflow clipping and white canvas.
    it("frames the master–detail in page gutters as a bordered box", async () => {
        const wrapper = mountPage();
        await nextTick();

        const gutter = wrapper.find("[data-test='admin-page-gutter']");
        expect(gutter.exists()).toBe(true);
        for (const cls of ["w-full", "h-full", "p-4", "md:p-8"]) {
            expect(gutter.classes()).toContain(cls);
        }

        const shell = wrapper.find("[data-test='users-shell']");
        expect(shell.exists()).toBe(true);
        for (const cls of ["flex", "w-full", "h-full", "overflow-hidden", "bg-white", "dark:bg-dark-canvas",
            "border", "border-gray-200", "rounded-xl", "dark:border-dark-border"]) {
            expect(shell.classes()).toContain(cls);
        }
        for (const cls of ["shadow", "ring-1"]) {
            expect(shell.classes()).not.toContain(cls);
        }
    });
});
