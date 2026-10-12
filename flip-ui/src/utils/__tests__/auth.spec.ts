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

import { createPinia, setActivePinia } from "pinia";

import { makeMockAuthProvider, NO_CAPABILITIES, resetMockAuthProvider } from "@/auth/__tests__/mock-provider";
import { SignInStep } from "@/auth/provider";
import router, { routeChange } from "@/router";
import { type SignInStep as SignInStepType, useAuthStore } from "@/store/auth";
import { authCheck, handleSessionExpired, isUserUnconfirmedCheck, NO_FORCED_SIGNOUT_PATHS } from "@/utils/auth";
import { leaveToLogin, stashPostSignOutNotice } from "@/utils/session-teardown";
import { Snackbar } from "@/utils/snackbar";

// The guard reaches the identity provider only through the store
// (`hasSession`, `fetchInfo`, `capabilities`); the provider is a bag of spies.
const authProvider = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ getAuthProvider: () => authProvider.current }));

vi.mock("@/router", () => ({
    default: {
        push: vi.fn(),
        currentRoute: {
            value: {
                path: "/",
                fullPath: "/"
            }
        }
    },
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

// Both forced sign-out paths here (the guard's catch and the session-expiry
// handler) end the session by discarding the page (utils/session-teardown.ts). jsdom
// cannot navigate, so the helper is stubbed; the tests assert it is called instead of
// a router push, and that the notice is queued for the reload rather than shown.
vi.mock("@/utils/session-teardown", () => ({
    leaveToLogin: vi.fn(),
    stashPostSignOutNotice: vi.fn()
}));

type Next = (path?: string) => void;

function makeNext(): { next: Next; calls: (string | undefined)[] } {
    const calls: (string | undefined)[] = [];
    const next: Next = (path?: string) => {
        calls.push(path);
    };

    return {
        next,
        calls
    };
}

// Minimal RouteLocationNormalized stand-in — authCheck only reads `path`.
function route(path: string): { path: string } {
    return { path };
}

const USER = {
    username: "u",
    userId: "id",
    attributes: {
        sub: "s",
        email: "u@e.com"
    },
    permissions: [] as string[]
};

const provider = makeMockAuthProvider();

describe("authCheck", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
        resetMockAuthProvider(provider);
        authProvider.current = provider;
        vi.mocked(routeChange.gotoLogin).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        vi.mocked(leaveToLogin).mockReset();
        vi.mocked(stashPostSignOutNotice).mockReset();
        // Reset env each time — some tests touch VITE_LOCAL.
        vi.unstubAllEnvs();
    });

    it("bypasses auth check on unguarded routes", async () => {
        const { next, calls } = makeNext();

        // Cast is safe — authCheck only reads route.path.
        await authCheck(route("/auth/login") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
        expect(provider.hasSession).not.toHaveBeenCalled();
    });

    it("bypasses auth check when VITE_LOCAL=true", async () => {
        vi.stubEnv("VITE_LOCAL", "true");
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
        expect(provider.hasSession).not.toHaveBeenCalled();
    });

    it("redirects to /auth/login when the provider has no session", async () => {
        provider.hasSession.mockResolvedValue(false);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.signInStep = SignInStep.DONE;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(auth.user).toBeNull();
        expect(auth.signInStep).toBeNull();
        expect(calls).toEqual(["/auth/login"]);
        // A clean redirect, not the "you've been signed out" teardown.
        expect(leaveToLogin).not.toHaveBeenCalled();
        expect(stashPostSignOutNotice).not.toHaveBeenCalled();
    });

    it("routes new-password challenge users to /auth/new-password before asking for a session", async () => {
        // Mid-challenge there is no session yet; the session check must
        // not run first and bounce the user to login.
        provider.hasSession.mockResolvedValue(false);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = SignInStep.NEW_PASSWORD_REQUIRED;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/new-password"]);
        expect(provider.hasSession).not.toHaveBeenCalled();
    });

    it("routes first-time MFA enrolment (TOTP setup) to /auth/mfa-setup", async () => {
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_SETUP;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("lets a TOTP-setup-challenge user stay on /auth/mfa-setup without recursing", async () => {
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_SETUP;

        await authCheck(route("/auth/mfa-setup") as never, route("/") as never, next as never);

        // next() with no argument — no further redirect.
        expect(calls).toEqual([undefined]);
    });

    it("routes TOTP-code challenge users to /auth/mfa-verify", async () => {
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_CODE;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-verify"]);
    });

    it("lets a TOTP-code-challenge user stay on /auth/mfa-verify without recursing", async () => {
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_CODE;

        await authCheck(route("/auth/mfa-verify") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("loads user info when session valid but store empty, then continues", async () => {
        provider.hasSession.mockResolvedValue(true);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        // Stub fetchInfo so we avoid exercising the real hydrate chain.
        const fetchInfo = vi.fn(async () => {
            auth.user = { ...USER };
            auth.mfaEnabled = true;
            auth.mfaRequired = true;
            auth.signInStep = SignInStep.DONE;
        });
        auth.fetchInfo = fetchInfo;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(fetchInfo).toHaveBeenCalledTimes(1);
        expect(calls).toEqual([undefined]);
    });

    it("bounces MFA-pending user (mfaEnabled=false) to /auth/mfa-setup when env requires MFA", async () => {
        provider.hasSession.mockResolvedValue(true);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.mfaEnabled = false;
        auth.mfaRequired = true;
        auth.signInStep = SignInStep.DONE;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("does NOT redirect an unenrolled user to /auth/mfa-setup when the env disables MFA", async () => {
        // Dev-only case: ENFORCE_MFA=false on the backend propagates to
        // authStore.mfaRequired=false. Users without TOTP get the same
        // router treatment as users who have TOTP — straight to the app.
        provider.hasSession.mockResolvedValue(true);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.mfaEnabled = false;
        auth.mfaRequired = false;
        auth.signInStep = SignInStep.DONE;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("does NOT redirect an unenrolled user to /auth/mfa-setup when the backend cannot enrol TOTP in-app", async () => {
        // Keycloak: MFA is managed in its own console, so the enrolment
        // page would have nothing to offer.
        authProvider.current = makeMockAuthProvider({
            backend: "keycloak",
            capabilities: NO_CAPABILITIES
        });
        (authProvider.current as ReturnType<typeof makeMockAuthProvider>).hasSession.mockResolvedValue(true);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.mfaEnabled = false;
        auth.mfaRequired = true;
        auth.signInStep = SignInStep.DONE;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("allows MFA-pending user to stay on /auth/mfa-setup", async () => {
        // Post-auth (signed in, mfaRequired=true, mfaEnabled=false) the
        // user is legitimately on /auth/mfa-setup. The mfaRequired check
        // already short-circuits with a same-path guard, so we should
        // pass through without a redirect.
        provider.hasSession.mockResolvedValue(true);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.mfaEnabled = false;
        auth.mfaRequired = true;
        auth.signInStep = SignInStep.DONE;

        await authCheck(route("/auth/mfa-setup") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("recovers from unexpected errors by resetting state, queuing a notice and tearing the page down", async () => {
        provider.hasSession.mockResolvedValue(true);
        const { next } = makeNext();
        const auth = useAuthStore();
        // Force fetchInfo to blow up so authCheck falls into its outer catch.
        auth.fetchInfo = vi.fn(async () => {
            throw new Error("boom");
        });
        const localStorageClear = vi.spyOn(Storage.prototype, "clear");

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(localStorageClear).toHaveBeenCalled();
        // Hard navigation, not a push: nothing of the ended session may stay in memory.
        expect(leaveToLogin).toHaveBeenCalledTimes(1);
        expect(routeChange.gotoLogin).not.toHaveBeenCalled();
        expect(stashPostSignOutNotice).toHaveBeenCalledWith({
            type: "error",
            title: "You've been signed out",
            text: "Please log in again to confirm your identity."
        });
        expect(Snackbar.error).not.toHaveBeenCalled();
        localStorageClear.mockRestore();
    });
});

describe("authCheck — Cypress hook (VITE_E2E build flag)", () => {
    // The src code branches at the top of authCheck on isCypressMode(),
    // which reads `import.meta.env.VITE_E2E` and is dead-code-eliminated
    // in non-E2E builds. Stubbing the env var lets unit tests drive the
    // same paths Cypress E2E exercises without touching a provider.

    beforeEach(() => {
        setActivePinia(createPinia());
        resetMockAuthProvider(provider);
        authProvider.current = provider;
        window.localStorage.clear();
        vi.stubEnv("VITE_E2E", "true");
    });

    afterEach(() => {
        vi.unstubAllEnvs();
        window.localStorage.clear();
    });

    it("redirects to /auth/login when no cypress.auth.user is set", async () => {
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/login"]);
        expect(provider.hasSession).not.toHaveBeenCalled();
    });

    it("populates the auth store from cypress.auth.user and continues", async () => {
        const user = {
            ...USER,
            permissions: ["CanManageProjects"]
        };
        window.localStorage.setItem("cypress.auth.user", JSON.stringify(user));
        const { next, calls } = makeNext();
        const auth = useAuthStore();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(auth.user).toEqual(user);
        expect(auth.signInStep).toBe(SignInStep.DONE);
        expect(calls).toEqual([undefined]);
        expect(provider.hasSession).not.toHaveBeenCalled();
    });

    it("routes new-password challenge users to /auth/new-password", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.NEW_PASSWORD_REQUIRED;
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/new-password"]);
    });

    it("lets new-password challenge users stay on /auth/new-password without recursing", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.NEW_PASSWORD_REQUIRED;
        const { next, calls } = makeNext();

        await authCheck(route("/auth/new-password") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("routes TOTP-setup challenge to /auth/mfa-setup", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_SETUP;
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("routes TOTP-code challenge to /auth/mfa-verify", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_CODE;
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-verify"]);
    });

    it("treats a malformed cypress.auth.user as 'no fixture' rather than nuking the session", async () => {
        // The earlier version threw inside JSON.parse and the umbrella
        // catch in authCheck would call $reset() + clear localStorage +
        // show "You've been signed out", masking the real cause from a
        // confused test author. We now log the error, drop the bad
        // fixture, and redirect to /auth/login deliberately.
        window.localStorage.setItem("cypress.auth.user", "{not json");
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/login"]);
        expect(consoleError).toHaveBeenCalled();
        expect(window.localStorage.getItem("cypress.auth.user")).toBeNull();
        // The umbrella "signed out" snackbar must NOT fire — that's the
        // generic error path we explicitly skirt.
        expect(Snackbar.error).not.toHaveBeenCalled();
        consoleError.mockRestore();
    });

    it("does not overwrite a preloaded auth.user", async () => {
        const auth = useAuthStore();
        auth.user = {
            ...USER,
            username: "preloaded"
        };
        window.localStorage.setItem(
            "cypress.auth.user",
            JSON.stringify({
                username: "fromStorage",
                userId: "x",
                attributes: {},
                permissions: []
            })
        );
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(auth.user.username).toBe("preloaded");
        expect(calls).toEqual([undefined]);
    });
});

describe("Cypress window hooks (VITE_E2E build flag)", () => {
    // auth.ts installs these onto `window` at module import time when the
    // VITE_E2E build flag is set. We resetModules + stubEnv so the
    // module-level installer runs under our control.

    type HookedWindow = {
        __cypressTriggerSessionExpiry?: () => void;
        __cypressGetAuthUser?: () => Promise<{ token: string | null; user: unknown }>;
    };

    const hooked = (): HookedWindow => window as unknown as HookedWindow;

    afterEach(() => {
        vi.unstubAllEnvs();
        vi.useRealTimers();
        delete hooked().__cypressTriggerSessionExpiry;
        delete hooked().__cypressGetAuthUser;
    });

    it("__cypressTriggerSessionExpiry runs the session-expiry handler", async () => {
        vi.stubEnv("VITE_E2E", "true");
        vi.resetModules();
        setActivePinia(createPinia());
        vi.useFakeTimers();
        router.currentRoute.value.path = "/projects";

        await import("@/utils/auth");
        const trigger = hooked().__cypressTriggerSessionExpiry;

        expect(typeof trigger).toBe("function");

        trigger?.();
        vi.advanceTimersByTime(200);

        // The re-imported module resolved fresh copies of the (mocked)
        // teardown helpers; assert through the copy it actually calls.
        const teardown = await import("@/utils/session-teardown");
        expect(teardown.leaveToLogin).toHaveBeenCalledTimes(1);
        expect(teardown.stashPostSignOutNotice).toHaveBeenCalledWith(
            expect.objectContaining({ title: "You've been signed out" })
        );
    });

    it("__cypressGetAuthUser returns the provider's token and identity, or a null token when signed out", async () => {
        vi.stubEnv("VITE_E2E", "true");
        vi.resetModules();
        resetMockAuthProvider(provider);
        authProvider.current = provider;

        await import("@/utils/auth");
        const getAuthUser = hooked().__cypressGetAuthUser;

        expect(typeof getAuthUser).toBe("function");

        await expect(getAuthUser?.()).resolves.toEqual({
            token: null,
            user: null
        });
        expect(provider.getUser).not.toHaveBeenCalled();

        provider.getAccessToken.mockResolvedValue("tok");
        provider.getUser.mockResolvedValue({
            sub: "s",
            email: "u@e.com",
            username: "u"
        });
        await expect(getAuthUser?.()).resolves.toEqual({
            token: "tok",
            user: {
                sub: "s",
                email: "u@e.com",
                username: "u"
            }
        });
    });

    it("does NOT install the hooks when VITE_E2E is unset", async () => {
        vi.stubEnv("VITE_E2E", "");
        vi.resetModules();
        // Make sure we're starting from a clean state.
        delete hooked().__cypressTriggerSessionExpiry;
        delete hooked().__cypressGetAuthUser;

        await import("@/utils/auth");

        expect(hooked().__cypressTriggerSessionExpiry).toBeUndefined();
        expect(hooked().__cypressGetAuthUser).toBeUndefined();
    });
});

describe("isUserUnconfirmedCheck", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
    });

    it("returns true only for the new-password challenge step", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.NEW_PASSWORD_REQUIRED;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(true);
    });

    it("returns false when the user is fully signed in (confirmedUser)", async () => {
        const auth = useAuthStore();
        auth.user = { ...USER };
        auth.signInStep = SignInStep.DONE;
        auth.mfaEnabled = true;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });

    it("returns false during TOTP challenge (not the step this page handles)", async () => {
        const auth = useAuthStore();
        auth.signInStep = SignInStep.TOTP_CODE;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });

    it("returns false when the user is signed out", async () => {
        const auth = useAuthStore();
        auth.user = null;
        auth.signInStep = null;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });

    it("returns false for any other step", async () => {
        const auth = useAuthStore();
        auth.signInStep = "SOME_UNHANDLED_STEP" as unknown as SignInStepType;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });
});

describe("handleSessionExpired", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(routeChange.gotoLogin).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        vi.mocked(leaveToLogin).mockReset();
        vi.mocked(stashPostSignOutNotice).mockReset();
        vi.useFakeTimers();
        router.currentRoute.value.path = "/projects";
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("no-ops on pre-auth / mid-challenge pages (no forced signout)", () => {
        router.currentRoute.value.path = "/auth/mfa-setup";

        handleSessionExpired();
        vi.advanceTimersByTime(200);

        expect(leaveToLogin).not.toHaveBeenCalled();
        expect(stashPostSignOutNotice).not.toHaveBeenCalled();
    });

    it("schedules a teardown with a queued notice on authenticated pages", () => {
        const auth = useAuthStore();
        auth.user = { ...USER };

        handleSessionExpired();
        vi.advanceTimersByTime(200);

        expect(stashPostSignOutNotice).toHaveBeenCalledWith({
            type: "error",
            title: "You've been signed out",
            text: "Your session has expired. Please log in again."
        });
        expect(leaveToLogin).toHaveBeenCalledTimes(1);
        expect(routeChange.gotoLogin).not.toHaveBeenCalled();
        expect(auth.user).toBeNull();
    });

    it("debounces a burst of expiry signals", () => {
        handleSessionExpired();
        handleSessionExpired();
        handleSessionExpired();
        vi.advanceTimersByTime(200);

        // Only the most recent scheduled timeout fires its callback.
        expect(leaveToLogin).toHaveBeenCalledTimes(1);
    });
});

describe("NO_FORCED_SIGNOUT_PATHS", () => {
    it("contains all pre-auth and mid-challenge paths", () => {
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/login")).toBe(true);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/new-password")).toBe(true);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/change-password")).toBe(true);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/access-request")).toBe(true);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/mfa-setup")).toBe(true);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/auth/mfa-verify")).toBe(true);
    });

    it("does not include authenticated paths", () => {
        expect(NO_FORCED_SIGNOUT_PATHS.has("/projects")).toBe(false);
        expect(NO_FORCED_SIGNOUT_PATHS.has("/")).toBe(false);
    });
});
