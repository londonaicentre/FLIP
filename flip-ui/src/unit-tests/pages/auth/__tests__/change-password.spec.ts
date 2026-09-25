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
