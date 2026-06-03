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

import { fetchAuthSession } from "aws-amplify/auth";
import { createPinia, setActivePinia } from "pinia";

import router, { routeChange } from "@/router";
import { type SignInStep, useAuthStore } from "@/store/auth";
import { authCheck, isUserUnconfirmedCheck, NO_FORCED_SIGNOUT_PATHS } from "@/utils/auth";
import { Snackbar } from "@/utils/snackbar";

vi.mock("aws-amplify/auth", () => ({ fetchAuthSession: vi.fn() }));

// The module eagerly calls Hub.listen at import time; stub the utils to
// keep the listener registration cheap and side-effect-free. Use
// vi.hoisted so the shared ref is defined before the mock factory runs
// (vi.mock is lifted to the top of the module at transform time). Tests
// below read `capturedListener.fn` to invoke the real callback even
// after vi.clearAllMocks() wipes mock.calls history.
const capturedListener = vi.hoisted(() => ({ fn: null as ((data: { payload: { event: string } }) => void) | null }));

vi.mock("aws-amplify/utils", () => ({
    Hub: {
        listen: vi.fn(
            (_channel: string, listener: (data: { payload: { event: string } }) => void) => {
                capturedListener.fn = listener;
            }
        ),
        dispatch: vi.fn()
    }
}));

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

describe("authCheck", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(fetchAuthSession).mockReset();
        vi.mocked(routeChange.gotoLogin).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        // Reset env each time — some tests touch VITE_LOCAL.
        vi.unstubAllEnvs();
    });

    it("bypasses auth check on unguarded routes", async () => {
        const { next, calls } = makeNext();

        // Cast is safe — authCheck only reads route.path.
        await authCheck(route("/auth/login") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
        expect(fetchAuthSession).not.toHaveBeenCalled();
    });

    it("bypasses auth check when VITE_LOCAL=true", async () => {
        vi.stubEnv("VITE_LOCAL", "true");
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
        expect(fetchAuthSession).not.toHaveBeenCalled();
    });

    it("redirects to /auth/login when there is no valid Amplify session", async () => {
        vi.mocked(fetchAuthSession).mockRejectedValue(new Error("no session"));
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };
        auth.signInStep = "DONE";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(auth.user).toBeNull();
        expect(auth.signInStep).toBeNull();
        expect(calls).toEqual(["/auth/login"]);
    });

    it("routes new-password challenge users to /auth/new-password", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/new-password"]);
    });

    it("routes first-time MFA enrolment (TOTP setup) to /auth/mfa-setup", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("lets a TOTP-setup-challenge user stay on /auth/mfa-setup without recursing", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";

        await authCheck(route("/auth/mfa-setup") as never, route("/") as never, next as never);

        // next() with no argument — no further redirect.
        expect(calls).toEqual([undefined]);
    });

    it("routes TOTP-code challenge users to /auth/mfa-verify", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-verify"]);
    });

    it("lets a TOTP-code-challenge user stay on /auth/mfa-verify without recursing", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";

        await authCheck(route("/auth/mfa-verify") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("loads user info when session valid but store empty, then continues", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        // Stub fetchInfo so we avoid exercising the real Amplify chain.
        const fetchInfo = vi.fn(async () => {
            auth.user = {
                username: "u",
                userId: "id",
                attributes: {
                    sub: "s",
                    email: "u@e.com"
                },
                permissions: []
            };
            auth.mfaEnabled = true;
            auth.mfaRequired = true;
            auth.signInStep = "DONE";
        });
        auth.fetchInfo = fetchInfo;

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(fetchInfo).toHaveBeenCalledTimes(1);
        expect(calls).toEqual([undefined]);
    });

    it("bounces MFA-pending user (mfaEnabled=false) to /auth/mfa-setup when env requires MFA", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };
        auth.mfaEnabled = false;
        auth.mfaRequired = true;
        auth.signInStep = "DONE";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("does NOT redirect an unenrolled user to /auth/mfa-setup when the env disables MFA", async () => {
        // Dev-only case: ENFORCE_MFA=false on the backend propagates to
        // authStore.mfaRequired=false. Users without TOTP get the same
        // router treatment as users who have TOTP — straight to the app.
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };
        auth.mfaEnabled = false;
        auth.mfaRequired = false;
        auth.signInStep = "DONE";

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("allows MFA-pending user to stay on /auth/mfa-setup", async () => {
        // Post-auth (signed in, mfaRequired=true, mfaEnabled=false) the
        // user is legitimately on /auth/mfa-setup. The mfaRequired check
        // already short-circuits with a same-path guard, so we should
        // pass through without a redirect.
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next, calls } = makeNext();
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };
        auth.mfaEnabled = false;
        auth.mfaRequired = true;
        auth.signInStep = "DONE";

        await authCheck(route("/auth/mfa-setup") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("recovers from unexpected errors by resetting state and showing snackbar", async () => {
        vi.mocked(fetchAuthSession).mockResolvedValue({} as never);
        const { next } = makeNext();
        const auth = useAuthStore();
        // Force fetchInfo to blow up so authCheck falls into its outer catch.
        auth.fetchInfo = vi.fn(async () => {
            throw new Error("boom");
        });
        const localStorageClear = vi.spyOn(Storage.prototype, "clear");

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(localStorageClear).toHaveBeenCalled();
        expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
        expect(Snackbar.error).toHaveBeenCalledWith({
            title: "You've been signed out",
            text: "Please log in again to confirm your identity."
        });
        localStorageClear.mockRestore();
    });
});

describe("authCheck — Cypress hook (VITE_E2E build flag)", () => {
    // The src code branches at the top of authCheck on isCypressMode(),
    // which reads `import.meta.env.VITE_E2E` and is dead-code-eliminated
    // in non-E2E builds. Stubbing the env var lets unit tests drive the
    // same paths Cypress E2E exercises without spinning up Amplify.

    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(fetchAuthSession).mockReset();
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
        expect(fetchAuthSession).not.toHaveBeenCalled();
    });

    it("populates the auth store from cypress.auth.user and continues", async () => {
        const user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: ["CanManageProjects"]
        };
        window.localStorage.setItem("cypress.auth.user", JSON.stringify(user));
        const { next, calls } = makeNext();
        const auth = useAuthStore();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(auth.user).toEqual(user);
        expect(auth.signInStep).toBe("DONE");
        expect(calls).toEqual([undefined]);
        expect(fetchAuthSession).not.toHaveBeenCalled();
    });

    it("routes new-password challenge users to /auth/new-password", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/new-password"]);
    });

    it("lets new-password challenge users stay on /auth/new-password without recursing", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";
        const { next, calls } = makeNext();

        await authCheck(route("/auth/new-password") as never, route("/") as never, next as never);

        expect(calls).toEqual([undefined]);
    });

    it("routes TOTP-setup challenge to /auth/mfa-setup", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";
        const { next, calls } = makeNext();

        await authCheck(route("/projects") as never, route("/") as never, next as never);

        expect(calls).toEqual(["/auth/mfa-setup"]);
    });

    it("routes TOTP-code challenge to /auth/mfa-verify", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";
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
            username: "preloaded",
            userId: "p",
            attributes: {
                sub: "p",
                email: "p@e.com"
            },
            permissions: []
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

describe("__cypressTriggerSessionExpiry", () => {
    // auth.ts installs this onto `window` at module import time when the
    // VITE_E2E build flag is set. The session-expired Cypress spec
    // dispatches the Hub event through it without having to simulate a
    // Cognito refresh round-trip. We resetModules + stubEnv so the
    // module-level installer runs under our control.

    it("is a function that dispatches the tokenRefresh_failure Hub event", async () => {
        vi.stubEnv("VITE_E2E", "true");
        vi.resetModules();

        // Re-mock aws-amplify/utils for this resetModules scope so the
        // re-imported auth.ts captures *our* Hub.dispatch reference.
        const dispatch = vi.fn();
        vi.doMock("aws-amplify/utils", () => ({
            Hub: {
                listen: vi.fn(),
                dispatch
            }
        }));

        await import("@/utils/auth");

        const trigger = (window as unknown as {
            __cypressTriggerSessionExpiry?: () => void;
        }).__cypressTriggerSessionExpiry;

        expect(typeof trigger).toBe("function");

        trigger?.();

        expect(dispatch).toHaveBeenCalledWith("auth", { event: "tokenRefresh_failure" });

        vi.doUnmock("aws-amplify/utils");
        vi.unstubAllEnvs();
        delete (window as unknown as { __cypressTriggerSessionExpiry?: unknown })
            .__cypressTriggerSessionExpiry;
    });

    it("does NOT install the trigger when VITE_E2E is unset", async () => {
        vi.stubEnv("VITE_E2E", "");
        vi.resetModules();
        vi.doMock("aws-amplify/utils", () => ({
            Hub: {
                listen: vi.fn(),
                dispatch: vi.fn()
            }
        }));
        // Make sure we're starting from a clean state.
        delete (window as unknown as { __cypressTriggerSessionExpiry?: unknown })
            .__cypressTriggerSessionExpiry;

        await import("@/utils/auth");

        expect(
            (window as unknown as { __cypressTriggerSessionExpiry?: unknown })
                .__cypressTriggerSessionExpiry
        ).toBeUndefined();

        vi.doUnmock("aws-amplify/utils");
        vi.unstubAllEnvs();
    });
});

describe("isUserUnconfirmedCheck", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
    });

    it("returns true only for the new-password challenge step", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(true);
    });

    it("returns false when the user is fully signed in (confirmedUser)", async () => {
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };
        auth.signInStep = "DONE";
        auth.mfaEnabled = true;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });

    it("returns false during TOTP challenge (not the step this page handles)", async () => {
        const auth = useAuthStore();
        auth.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";

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
        auth.signInStep = "SOME_UNHANDLED_STEP" as unknown as SignInStep;

        await expect(isUserUnconfirmedCheck(auth)).resolves.toBe(false);
    });
});

describe("Hub listener (tokenRefresh_failure)", () => {
    type HubListener = (data: { payload: { event: string } }) => void;

    function getListener(): HubListener {
        if (!capturedListener.fn) {
            throw new Error("Hub listener was not captured at module load");
        }

        return capturedListener.fn;
    }

    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(routeChange.gotoLogin).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        vi.useFakeTimers();
        router.currentRoute.value.path = "/projects";
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("ignores events other than tokenRefresh_failure", () => {
        const listener = getListener();
        listener({ payload: { event: "signIn" } });

        vi.advanceTimersByTime(200);
        expect(routeChange.gotoLogin).not.toHaveBeenCalled();
    });

    it("no-ops on pre-auth / mid-challenge pages (no forced signout)", () => {
        router.currentRoute.value.path = "/auth/mfa-setup";
        const listener = getListener();

        listener({ payload: { event: "tokenRefresh_failure" } });
        vi.advanceTimersByTime(200);

        expect(routeChange.gotoLogin).not.toHaveBeenCalled();
        expect(Snackbar.error).not.toHaveBeenCalled();
    });

    it("schedules redirect + snackbar on authenticated pages", () => {
        const listener = getListener();
        const auth = useAuthStore();
        auth.user = {
            username: "u",
            userId: "id",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };

        listener({ payload: { event: "tokenRefresh_failure" } });
        vi.advanceTimersByTime(200);

        expect(Snackbar.error).toHaveBeenCalledWith({
            title: "You've been signed out",
            text: "Your session has expired. Please log in again."
        }, 60_000);
        expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
        expect(auth.user).toBeNull();
    });

    it("debounces a burst of tokenRefresh_failure events", () => {
        const listener = getListener();

        listener({ payload: { event: "tokenRefresh_failure" } });
        listener({ payload: { event: "tokenRefresh_failure" } });
        listener({ payload: { event: "tokenRefresh_failure" } });
        vi.advanceTimersByTime(200);

        // Only the most recent scheduled timeout fires its callback.
        expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
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
