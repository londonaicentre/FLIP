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
import { flushPromises, mount, VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { makeMockAuthProvider, NO_CAPABILITIES } from "@/auth/__tests__/mock-provider";
import ChangePassword from "@/pages/auth/change-password.vue";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";

const mockGotoLogin = vi.fn();

vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: { gotoLogin: (...args: unknown[]) => mockGotoLogin(...args) }
}));

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({ params: {} })
    };
});

const mockSnackbarShow = vi.fn();
const mockSnackbarSuccess = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: (...args: unknown[]) => mockSnackbarShow(...args),
        success: (...args: unknown[]) => mockSnackbarSuccess(...args),
        error: vi.fn()
    }
}));

// The page reads the store's `capabilities` getter, which comes from the
// provider behind the `@/auth` seam.
const authProvider = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ getAuthProvider: () => authProvider.current }));

function mountChangePassword(): VueWrapper {
    return mount(ChangePassword, {
        global: {
            plugins: [createTestingPinia({ createSpy: vi.fn })],
            stubs: {
                Form: {
                    template: "<form @submit.prevent=\"$emit('submit', { email: 'u@e.com' })\"><slot /></form>",
                    inheritAttrs: false,
                    emits: ["submit"]
                },
                AiInput: {
                    template: "<input />",
                    props: ["name", "type", "label", "preIcon", "initialValue"]
                },
                AiAlert: { template: "<div />" },
                AiButton: {
                    template: "<button :data-test=\"$attrs['data-test']\" @click=\"$emit('click')\"><slot /></button>",
                    props: ["primary", "light", "loading"],
                    emits: ["click"]
                }
            }
        }
    });
}

describe("change-password page", () => {
    beforeEach(() => {
        mockGotoLogin.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarSuccess.mockReset();
        authProvider.current = makeMockAuthProvider();
    });

    test("renders the request-code form when the backend has an in-app reset flow", async () => {
        const wrapper = mountChangePassword();
        await flushPromises();

        expect(mockGotoLogin).not.toHaveBeenCalled();
        expect(wrapper.find("[data-test='requestCode-btn']").exists()).toBe(true);
    });

    test("requesting a code goes through the store", async () => {
        const wrapper = mountChangePassword();
        await flushPromises();
        const authStore = useAuthStore();

        await wrapper.find("form").trigger("submit");
        await flushPromises();

        expect(authStore.resetPassword).toHaveBeenCalledWith("u@e.com");
        expect(wrapper.find("[data-test='changePassword-btn']").exists()).toBe(true);
    });

    test("stays on the request form and raises the error state when the code request fails", async () => {
        const wrapper = mountChangePassword();
        vi.mocked(useAuthStore().resetPassword).mockRejectedValueOnce(new Error("boom"));

        await wrapper.find("form").trigger("submit");
        await flushPromises();

        expect(useErrorStore().setError).toHaveBeenCalled();
        expect(wrapper.find("[data-test='requestCode-btn']").exists()).toBe(true);
    });

    test("switches between the two forms with 'I have a code' and 'I need a code'", async () => {
        const wrapper = mountChangePassword();

        await wrapper.find("[data-test='iHaveACode-btn']").trigger("click");
        expect(wrapper.find("h1").text()).toBe("Change your password");

        await wrapper.find("[data-test='iNeedACode-btn']").trigger("click");
        expect(wrapper.find("h1").text()).toBe("Request password change");
    });

    test("changes the password through the store, confirms it and returns to login", async () => {
        const wrapper = mountChangePassword();
        await wrapper.find("[data-test='iHaveACode-btn']").trigger("click");

        await wrapper.find("form").trigger("submit");
        await flushPromises();

        expect(useAuthStore().updateForgottenPassword).toHaveBeenCalledWith({ email: "u@e.com" });
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "Password Changed" }));
        expect(mockGotoLogin).toHaveBeenCalledTimes(1);
    });

    test("reports a failed change and sends the user back to request a new code", async () => {
        const wrapper = mountChangePassword();
        vi.mocked(useAuthStore().updateForgottenPassword).mockRejectedValueOnce(new Error("boom"));
        await wrapper.find("[data-test='iHaveACode-btn']").trigger("click");

        await wrapper.find("form").trigger("submit");
        await flushPromises();

        expect(mockSnackbarShow).toHaveBeenCalledWith(expect.objectContaining({ type: "error" }), 8_000);
        expect(useErrorStore().setError).toHaveBeenCalled();
        expect(mockGotoLogin).not.toHaveBeenCalled();
        expect(wrapper.find("[data-test='requestCode-btn']").exists()).toBe(true);
    });

    test("sends one change request when the form is submitted twice", async () => {
        const wrapper = mountChangePassword();
        vi.mocked(useAuthStore().updateForgottenPassword).mockReturnValueOnce(new Promise(() => {}));
        await wrapper.find("[data-test='iHaveACode-btn']").trigger("click");

        await wrapper.find("form").trigger("submit");
        await wrapper.find("form").trigger("submit");

        expect(useAuthStore().updateForgottenPassword).toHaveBeenCalledTimes(1);
    });

    test("bounces to login with a notice when the backend has no in-app reset flow", async () => {
        // Keycloak: Login.vue links to Keycloak's own reset page instead.
        authProvider.current = makeMockAuthProvider({
            backend: "keycloak",
            capabilities: NO_CAPABILITIES
        });

        mountChangePassword();
        await flushPromises();

        expect(mockGotoLogin).toHaveBeenCalledTimes(1);
        expect(mockSnackbarShow).toHaveBeenCalledWith(expect.objectContaining({ title: "Not available" }));
    });
});
