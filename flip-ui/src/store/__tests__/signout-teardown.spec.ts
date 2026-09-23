// Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * FLIP#995 — why signing out must discard the page, demonstrated rather than asserted.
 *
 * The first test reproduces the disclosure with the REAL swrv (deliberately not mocked): a
 * component subscribed to a constant key renders user A's data, the session is ended the way
 * `signOut` used to end it (store reset, no reload), and a fresh mount for user B is served
 * A's data straight from swrv's module-level cache — with B's own fetcher never called,
 * because `dedupingInterval` treats the stale entry as fresh. Nothing in a Vue app can reach
 * that cache to clear it: the default instance is not exported.
 *
 * The second test pins the fix: the real `signOut` ends in `leaveToLogin`, a hard navigation,
 * which is the only operation that discards that cache (along with every Pinia store and
 * closure). jsdom cannot perform the navigation, so the browser-level proof that the heap is
 * actually gone lives in Cypress: test/cypress/integration/group-3/auth/signout_teardown.spec.ts.
 */

import { flushPromises, mount } from "@vue/test-utils";
import { signOut as amplifySignOut } from "aws-amplify/auth";
import { createPinia, setActivePinia } from "pinia";
import useSWRV from "swrv";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";

import { useAuthStore } from "@/store/auth";
import { leaveToLogin, stashPostSignOutNotice } from "@/utils/session-teardown";

vi.mock("aws-amplify/auth", () => ({
    confirmResetPassword: vi.fn(),
    confirmSignIn: vi.fn(),
    fetchAuthSession: vi.fn(),
    fetchUserAttributes: vi.fn(),
    getCurrentUser: vi.fn(),
    resetPassword: vi.fn(),
    setUpTOTP: vi.fn(),
    signIn: vi.fn(),
    signOut: vi.fn(),
    updateMFAPreference: vi.fn(),
    verifyTOTPSetup: vi.fn()
}));
vi.mock("@/services/user-service", () => ({
    getMfaStatus: vi.fn(),
    getUserPermissions: vi.fn()
}));
vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: { gotoLogin: vi.fn() }
}));
vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));
vi.mock("@/utils/session-teardown", () => ({
    leaveToLogin: vi.fn(),
    stashPostSignOutNotice: vi.fn()
}));

// A constant key, as `/projects`, `/models/projects`, `/users` and `/cohort/<id>` are in the tree.
const KEY = "/projects#flip-995-repro";

const projectList = (fetcher: () => Promise<string[]>) => defineComponent({
    setup() {
        const { data } = useSWRV(KEY, fetcher, {
            dedupingInterval: 60_000,
            revalidateOnFocus: false
        });

        return () => h("ul", (data.value ?? []).map((name) => h("li", name)));
    }
});

describe("FLIP#995 — sign-out must discard the page", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(leaveToLogin).mockReset();
        vi.mocked(stashPostSignOutNotice).mockReset();
    });

    it("reproduces the leak: after a reset-only sign-out, the next account is served the previous user's data from cache", async () => {
        const fetchAsA = vi.fn(async () => ["A-secret-project"]);
        const fetchAsB = vi.fn(async () => ["B-project"]);

        // User A signs in and loads their projects.
        const auth = useAuthStore();
        auth.user = {
            username: "a",
            userId: "a",
            attributes: {
                sub: "a",
                email: "a@x"
            },
            permissions: []
        };
        const asA = mount(projectList(fetchAsA));
        await flushPromises();
        expect(asA.text()).toContain("A-secret-project");
        expect(fetchAsA).toHaveBeenCalledTimes(1);

        // The session ends the way signOut used to end it: store reset, page left running.
        asA.unmount();
        auth.$reset();
        expect(auth.user).toBeNull();

        // User B signs in to the same tab and opens the same page.
        auth.user = {
            username: "b",
            userId: "b",
            attributes: {
                sub: "b",
                email: "b@x"
            },
            permissions: []
        };
        const asB = mount(projectList(fetchAsB));

        // Rendered synchronously, before B's fetcher could possibly have answered:
        expect(asB.text()).toContain("A-secret-project");
        expect(asB.text()).not.toContain("B-project");
        // ...and it is not a flash that a refetch corrects: within dedupingInterval swrv
        // treats A's entry as fresh, so B's fetcher is never called at all.
        await flushPromises();
        expect(fetchAsB).not.toHaveBeenCalled();
        expect(asB.text()).toContain("A-secret-project");
        asB.unmount();
    });

    it("the fix: the real signOut ends in a hard navigation, never a route push", async () => {
        vi.mocked(amplifySignOut).mockResolvedValue(undefined as never);
        const auth = useAuthStore();
        auth.user = {
            username: "a",
            userId: "a",
            attributes: {
                sub: "a",
                email: "a@x"
            },
            permissions: []
        };
        auth.signInStep = "DONE";

        await auth.signOut();

        expect(auth.user).toBeNull();
        // leaveToLogin is window.location.assign("/auth/login") — the document, and with it the
        // swrv cache the test above rendered from, is discarded. A router push would not do that.
        expect(leaveToLogin).toHaveBeenCalledTimes(1);
        const { routeChange } = await import("@/router");
        expect(routeChange.gotoLogin).not.toHaveBeenCalled();
    });
});
