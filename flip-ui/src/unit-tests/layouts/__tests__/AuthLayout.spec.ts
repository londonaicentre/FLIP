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
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AuthLayout from "@/layouts/AuthLayout.vue";
import { useAuthStore } from "@/store/auth";

// The layout uses useRoute() to pick the current page — mock it per test so
// we can simulate navigation to Login, mfa-verify, mfa-setup etc.
const currentRoute = {
    name: "",
    path: ""
};

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => currentRoute
    };
});

// The auth store pulls in @/router at import time (which would instantiate
// a real router with generated routes). Stub it — the layout itself no
// longer uses router.push, but the store's transitive import still does.
vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: { gotoLogin: vi.fn() }
}));

const mockAmplifySignOut = vi.fn();

vi.mock("aws-amplify/auth", () => ({ signOut: (...args: unknown[]) => mockAmplifySignOut(...args) }));

function mountLayout(route: { name: string; path: string }): VueWrapper {
    currentRoute.name = route.name;
    currentRoute.path = route.path;

    return mount(AuthLayout, {
        global: {
            plugins: [createTestingPinia({ createSpy: vi.fn })],
            stubs: {
                AiErrorAlert: true,
                LoginBranding: true,
                "router-view": true
            }
        }
    });
}

describe("AuthLayout — Back to log in button", () => {
    let locationAssign: ReturnType<typeof vi.fn>;
    let originalLocation: Location;

    beforeEach(() => {
        currentRoute.name = "";
        currentRoute.path = "";
        mockAmplifySignOut.mockReset();
        mockAmplifySignOut.mockResolvedValue(undefined);
        // jsdom ignores assignments to window.location.href, so spy on
        // .assign to verify the hard-navigation happens. Stash the real
        // location so we can restore it — otherwise the stub leaks into
        // any subsequent test in the same worker that touches location.
        originalLocation = window.location;
        locationAssign = vi.fn();
        Object.defineProperty(window, "location", {
            configurable: true,
            value: {
                ...window.location,
                assign: locationAssign
            }
        });
    });

    afterEach(() => {
        Object.defineProperty(window, "location", {
            configurable: true,
            value: originalLocation
        });
    });

    test("is hidden on the Login page itself (already there)", () => {
        const wrapper = mountLayout({
            name: "auth-Login",
            path: "/auth/login"
        });

        expect(wrapper.find("[data-test='back-to-login']").exists()).toBe(false);
    });

    test("is rendered on the MFA-verify page", () => {
        const wrapper = mountLayout({
            name: "auth-mfa-verify",
            path: "/auth/mfa-verify"
        });

        expect(wrapper.find("[data-test='back-to-login']").exists()).toBe(true);
    });

    test("is rendered on the MFA-setup page", () => {
        const wrapper = mountLayout({
            name: "auth-mfa-setup",
            path: "/auth/mfa-setup"
        });

        expect(wrapper.find("[data-test='back-to-login']").exists()).toBe(true);
    });

    test("clicking from mfa-verify resets the store, clears localStorage, and hard-navigates to /auth/login", async () => {
        // Regression: soft Vue Router navigations were getting swallowed
        // after a failed MFA attempt (Amplify + router-guard could bounce
        // the user straight back). The handler must do a hard navigation
        // so nothing can short-circuit it.
        const wrapper = mountLayout({
            name: "auth-mfa-verify",
            path: "/auth/mfa-verify"
        });
        const authStore = useAuthStore();
        authStore.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";
        const localStorageClear = vi.spyOn(Storage.prototype, "clear");

        await wrapper.find("[data-test='back-to-login']").trigger("click");

        expect(mockAmplifySignOut).toHaveBeenCalledTimes(1);
        expect(authStore.$reset).toHaveBeenCalledTimes(1);
        expect(localStorageClear).toHaveBeenCalled();
        expect(locationAssign).toHaveBeenCalledWith("/auth/login");

        localStorageClear.mockRestore();
    });

    test("does not await Amplify signOut (which can hang on challenge-only sessions)", async () => {
        // If the previous implementation `await`-ed amplifySignOut and that
        // call hung on a mid-challenge session, the store reset and
        // navigation would never fire. The handler must navigate regardless.
        let resolveSignOut!: () => void;
        mockAmplifySignOut.mockImplementationOnce(
            () => new Promise<void>(resolve => { resolveSignOut = resolve; })
        );

        const wrapper = mountLayout({
            name: "auth-mfa-verify",
            path: "/auth/mfa-verify"
        });
        const authStore = useAuthStore();

        await wrapper.find("[data-test='back-to-login']").trigger("click");

        expect(authStore.$reset).toHaveBeenCalledTimes(1);
        expect(locationAssign).toHaveBeenCalledWith("/auth/login");

        resolveSignOut();
        await flushPromises();
    });
});
