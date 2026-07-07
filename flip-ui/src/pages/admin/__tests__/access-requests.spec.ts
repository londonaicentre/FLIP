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
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { IAccessRequestRecord } from "@/services/user-service";

import AccessRequestsPage from "../access-requests.vue";

interface IRequestPage {
    data: IAccessRequestRecord[];
    totalPages: number;
    page: number;
    pageSize: number;
    totalRecords: number;
}

interface IRolesPage {
    roles: { id: string; rolename: string; roledescription: string }[];
}

const requestPage = ref<IRequestPage | undefined>(undefined);
const rolesPage = ref<IRolesPage | undefined>(undefined);
const mutateRequests = vi.fn();

vi.mock("swrv", () => ({
    default: (keyFn: () => string) => {
        const key = typeof keyFn === "function" ? keyFn() : keyFn;
        // The page requests /users/access… then /roles. Route by key prefix.
        if (key.startsWith("/users/access")) {
            return {
                data: requestPage,
                mutate: mutateRequests,
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

const mockUpdateAccessRequestStatus = vi.fn();

vi.mock("@/services/user-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/user-service")>();

    return {
        ...actual,
        updateAccessRequestStatus: (...args: unknown[]) => mockUpdateAccessRequestStatus(...args)
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
    AiCard: { template: "<div><slot /></div>" },
    AiButton: {
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" @click=\"$emit('click', $event)\"><slot /></button>"
    },
    AiSkeleton: { template: "<div data-test='skeleton' />" },
    AiPagination: {
        name: "AiPagination",
        props: ["totalPages", "currentPage"],
        emits: ["page-update"],
        template: "<div data-test='pagination' />"
    },
    AiConfirmModal: {
        name: "AiConfirmModal",
        props: ["dialog", "continueAction", "confirmationText"],
        emits: ["close-modal"],
        template: "<div data-test='confirm-modal' :data-dialog='dialog' :data-text='confirmationText' />"
    },
    RegisterUserModal: {
        name: "RegisterUserModal",
        props: ["dialog", "initialName", "initialEmail", "roles"],
        emits: ["close-modal", "on-success"],
        template: "<div data-test='register-user-modal' :data-dialog='dialog' />"
    }
};

const REQUESTS: IAccessRequestRecord[] = [
    {
        id: "ar-1",
        email: "pending@example.test",
        fullName: "Pending Person",
        reasonForAccess: "research collaboration",
        status: "PENDING",
        emailNotified: false,
        handledByUserId: null,
        createdAt: "2026-07-01T10:00:00",
        updatedAt: "2026-07-01T10:00:00"
    },
    {
        id: "ar-2",
        email: "enrolled@example.test",
        fullName: "Enrolled Person",
        reasonForAccess: "audit",
        status: "ENROLLED",
        emailNotified: true,
        handledByUserId: "admin-1",
        createdAt: "2026-06-30T09:00:00",
        updatedAt: "2026-06-30T12:00:00"
    }
];

const ROLES = [
    {
        id: "r-viewer",
        rolename: "Viewer",
        roledescription: "Read-only"
    }
];

function mountPage(options: { requests?: IAccessRequestRecord[] } = {}) {
    const { requests = REQUESTS } = options;
    requestPage.value = {
        data: requests,
        totalPages: 1,
        page: 1,
        pageSize: 20,
        totalRecords: requests.length
    };
    rolesPage.value = { roles: ROLES };

    return mount(AccessRequestsPage, {
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

beforeEach(() => {
    requestPage.value = undefined;
    rolesPage.value = undefined;
    mutateRequests.mockReset();
    mockUpdateAccessRequestStatus.mockReset();
    mockSnackbarSuccess.mockReset();
    mockSnackbarError.mockReset();
    mockRouteChangeViewProjects.mockReset();
});

describe("Access Requests page", () => {
    it("renders a row per access request", () => {
        const wrapper = mountPage();

        const rows = wrapper.findAll("[data-test='access-request-row']");
        expect(rows).toHaveLength(2);
        expect(wrapper.text()).toContain("Pending Person");
        expect(wrapper.text()).toContain("Enrolled Person");
    });

    it("shows Enroll/Dismiss actions only for pending requests", () => {
        const wrapper = mountPage();

        // Only the single PENDING row exposes the action buttons.
        expect(wrapper.findAll("[data-test='enroll-btn']")).toHaveLength(1);
        expect(wrapper.findAll("[data-test='dismiss-btn']")).toHaveLength(1);
    });

    it("opens the register-user modal pre-filled when Enroll is clicked", async () => {
        const wrapper = mountPage();

        await wrapper.find("[data-test='enroll-btn']").trigger("click");

        const modal = wrapper.findComponent({ name: "RegisterUserModal" });
        expect(modal.props("dialog")).toBe(true);
        expect(modal.props("initialEmail")).toBe("pending@example.test");
        expect(modal.props("initialName")).toBe("Pending Person");
    });

    it("marks the request ENROLLED after the register-user flow succeeds", async () => {
        mockUpdateAccessRequestStatus.mockResolvedValue({});
        const wrapper = mountPage();

        await wrapper.find("[data-test='enroll-btn']").trigger("click");
        await wrapper.findComponent({ name: "RegisterUserModal" }).vm.$emit("on-success");
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).toHaveBeenCalledWith("ar-1", "ENROLLED");
        expect(mutateRequests).toHaveBeenCalled();
    });

    it("dismisses a request via the confirm modal", async () => {
        mockUpdateAccessRequestStatus.mockResolvedValue({});
        const wrapper = mountPage();

        await wrapper.find("[data-test='dismiss-btn']").trigger("click");

        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        expect(modal.props("dialog")).toBe(true);
        await modal.props("continueAction")();
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).toHaveBeenCalledWith("ar-1", "DISMISSED");
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "Request dismissed" }));
        expect(mutateRequests).toHaveBeenCalled();
    });

    it("redirects a caller lacking CanManageUsers", async () => {
        const { canAccessRoute } = await import("@/utils/route-validator");
        vi.mocked(canAccessRoute).mockResolvedValueOnce(false);

        mountPage();
        await flushPromises();

        expect(mockRouteChangeViewProjects).toHaveBeenCalled();
    });

    it("shows an empty message when there are no requests", () => {
        const wrapper = mountPage({ requests: [] });

        expect(wrapper.find("[data-test='access-requests-empty']").exists()).toBe(true);
        expect(wrapper.findAll("[data-test='access-request-row']")).toHaveLength(0);
    });

    it("surfaces an error when marking a request ENROLLED fails", async () => {
        // The Cognito user was already created by the register flow, so a
        // failed status update must be surfaced (not silently swallowed) and
        // flag the global error state — but it is not fatal.
        mockUpdateAccessRequestStatus.mockRejectedValue(new Error("500 Server Error"));
        const wrapper = mountPage();

        await wrapper.find("[data-test='enroll-btn']").trigger("click");
        await wrapper.findComponent({ name: "RegisterUserModal" }).vm.$emit("on-success");
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).toHaveBeenCalledWith("ar-1", "ENROLLED");
        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Couldn't update request" }));
    });

    it("surfaces an error when dismissing a request fails", async () => {
        mockUpdateAccessRequestStatus.mockRejectedValue(new Error("500 Server Error"));
        const wrapper = mountPage();

        await wrapper.find("[data-test='dismiss-btn']").trigger("click");
        await wrapper.findComponent({ name: "AiConfirmModal" }).props("continueAction")();
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).toHaveBeenCalledWith("ar-1", "DISMISSED");
        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Couldn't dismiss request" }));
    });

    it("ignores an on-success emit when no request is selected", async () => {
        const wrapper = mountPage();

        // No prior Enroll click -> selectedRequest is undefined -> no-op.
        await wrapper.findComponent({ name: "RegisterUserModal" }).vm.$emit("on-success");
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).not.toHaveBeenCalled();
    });

    it("ignores a dismiss confirm when no request is selected", async () => {
        const wrapper = mountPage();

        // No prior Dismiss click -> selectedRequest is undefined -> no-op.
        await wrapper.findComponent({ name: "AiConfirmModal" }).props("continueAction")();
        await flushPromises();

        expect(mockUpdateAccessRequestStatus).not.toHaveBeenCalled();
    });

    it("resets to the first page when the status filter changes", async () => {
        const wrapper = mountPage();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const setupState = (wrapper.vm as any).$.setupState;

        wrapper.findComponent({ name: "AiPagination" }).vm.$emit("page-update", 3);
        await flushPromises();
        expect(setupState.pageNumber).toBe(3);

        await wrapper.find("[data-test='status-filter']").setValue("ENROLLED");
        expect(setupState.pageNumber).toBe(1);
    });

    it("closes the enroll modal on its close-modal event", async () => {
        const wrapper = mountPage();

        await wrapper.find("[data-test='enroll-btn']").trigger("click");
        expect(wrapper.findComponent({ name: "RegisterUserModal" }).props("dialog")).toBe(true);

        await wrapper.findComponent({ name: "RegisterUserModal" }).vm.$emit("close-modal");
        expect(wrapper.findComponent({ name: "RegisterUserModal" }).props("dialog")).toBe(false);
    });

    it("closes the dismiss modal on its close-modal event", async () => {
        const wrapper = mountPage();

        await wrapper.find("[data-test='dismiss-btn']").trigger("click");
        expect(wrapper.findComponent({ name: "AiConfirmModal" }).props("dialog")).toBe(true);

        await wrapper.findComponent({ name: "AiConfirmModal" }).vm.$emit("close-modal");
        expect(wrapper.findComponent({ name: "AiConfirmModal" }).props("dialog")).toBe(false);
    });
});

describe("admin subpage gutters", () => {
    // admin.vue no longer pads the content wrapper (Users runs edge-to-edge),
    // so carded subpages own their gutters via this wrapper.
    it("wraps the card in a gutter that owns the page padding", () => {
        const wrapper = mountPage();

        const gutter = wrapper.find("[data-test='admin-page-gutter']");
        expect(gutter.exists()).toBe(true);
        for (const cls of ["w-full", "h-full", "p-4", "md:p-8"]) {
            expect(gutter.classes()).toContain(cls);
        }
    });
});
