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
import axios from "axios";
import { createPinia, setActivePinia } from "pinia";

import { _http } from "@/services/api";
import { useAuthStore } from "@/store/auth";
import { Snackbar } from "@/utils/snackbar";

vi.mock("aws-amplify/auth", () => ({
    fetchAuthSession: vi.fn()
}));

// utils/auth registers a Hub.listen at import time; stub it so pulling in
// api.ts (which transitively imports utils/auth for NO_FORCED_SIGNOUT_PATHS)
// stays side-effect-free.
vi.mock("aws-amplify/utils", () => ({
    Hub: { listen: vi.fn() }
}));

vi.mock("@/router", () => ({
    default: { push: vi.fn(), currentRoute: { value: { fullPath: "/" } } },
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

// The store is too heavy to drive through its real actions here; we only
// need to verify that a 401 triggers signOut(). Stubbing the action
// avoids executing the Amplify chain inside the interceptor.
const mockStoreSignOut = vi.fn();

/**
 * Axios interceptor registry — populated by `axios.create` via a spy so
 * tests can invoke the real request/response handlers without issuing
 * HTTP calls.
 */
type RequestFn = (config: Record<string, unknown>) => Promise<Record<string, unknown>>;
type RequestErrFn = (err: unknown) => unknown;
type ResponseFn = (response: unknown) => unknown;
type ResponseErrFn = (err: unknown) => unknown;

const interceptors: {
    requestOnFulfilled?: RequestFn;
    requestOnRejected?: RequestErrFn;
    responseOnFulfilled?: ResponseFn;
    responseOnRejected?: ResponseErrFn;
} = {};

const fakeAxiosInstance = {
    interceptors: {
        request: {
            use: vi.fn((fulfilled: RequestFn, rejected: RequestErrFn) => {
                interceptors.requestOnFulfilled = fulfilled;
                interceptors.requestOnRejected = rejected;
            })
        },
        response: {
            use: vi.fn((fulfilled: ResponseFn, rejected: ResponseErrFn) => {
                interceptors.responseOnFulfilled = fulfilled;
                interceptors.responseOnRejected = rejected;
            })
        }
    },
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn()
};

vi.mock("axios", () => ({
    default: {
        create: vi.fn(() => fakeAxiosInstance)
    }
}));

describe("api.ts Http client", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
        vi.mocked(fetchAuthSession).mockReset();
        vi.mocked(Snackbar.show).mockReset();
        mockStoreSignOut.mockReset();
        fakeAxiosInstance.get.mockReset();
        fakeAxiosInstance.post.mockReset();
        fakeAxiosInstance.put.mockReset();
        fakeAxiosInstance.delete.mockReset();
        fakeAxiosInstance.interceptors.request.use.mockClear();
        fakeAxiosInstance.interceptors.response.use.mockClear();
        // Reset the singleton's cached axios instance so each test starts
        // fresh and re-registers interceptors.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (_http as any).instance = null;
        interceptors.requestOnFulfilled = undefined;
        interceptors.requestOnRejected = undefined;
        interceptors.responseOnFulfilled = undefined;
        interceptors.responseOnRejected = undefined;
        const auth = useAuthStore();
        auth.signOut = mockStoreSignOut;
    });

    function primeHttp(): void {
        // Trigger init by calling any method — we stub to resolve immediately.
        fakeAxiosInstance.get.mockResolvedValue({ data: {} });
        _http.get("/ping");
    }

    describe("request interceptor", () => {
        it("attaches Bearer token from the current Amplify session", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValue({
                tokens: { accessToken: { toString: () => "token-abc" } }
            } as never);
            primeHttp();

            const cfg = { headers: {} } as Record<string, unknown>;
            const out = await interceptors.requestOnFulfilled!(cfg);

            expect((out.headers as Record<string, string>).Authorization).toBe(
                "Bearer token-abc"
            );
        });

        it("skips injection when Authorization is already explicitly set", async () => {
            primeHttp();

            const cfg = { headers: { Authorization: "" } };
            const out = await interceptors.requestOnFulfilled!(cfg);

            expect(fetchAuthSession).not.toHaveBeenCalled();
            expect((out.headers as Record<string, string>).Authorization).toBe("");
        });

        it("returns the config unchanged when no accessToken is present", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValue({ tokens: undefined } as never);
            primeHttp();

            const cfg = { headers: {} } as Record<string, unknown>;
            const out = await interceptors.requestOnFulfilled!(cfg);

            expect((out.headers as Record<string, string>).Authorization).toBeUndefined();
        });

        it("logs a warning when forceRefresh throws so the cause is visible in DevTools", async () => {
            // First fetchAuthSession returns no tokens, then forceRefresh
            // throws — without the warn log, the user would just be
            // signed out by the 401 handler with no clue which Amplify
            // class fired (TooManyRequestsException, network error, etc.).
            vi.mocked(fetchAuthSession)
                .mockResolvedValueOnce({ tokens: undefined } as never)
                .mockRejectedValueOnce(Object.assign(new Error("throttle"), { name: "TooManyRequestsException" }));
            const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
            primeHttp();

            const cfg = { headers: {} } as Record<string, unknown>;
            const out = await interceptors.requestOnFulfilled!(cfg);

            expect((out.headers as Record<string, string>).Authorization).toBeUndefined();
            expect(consoleWarn).toHaveBeenCalledWith(
                "Token forceRefresh failed:",
                expect.objectContaining({ name: "TooManyRequestsException" })
            );
            consoleWarn.mockRestore();
        });

        it("rejects when request preparation errors", async () => {
            primeHttp();
            const err = new Error("cfg failed");
            await expect(interceptors.requestOnRejected!(err)).rejects.toBe(err);
        });
    });

    describe("response interceptor", () => {
        it("passes successful responses through unchanged", () => {
            primeHttp();
            const response = { status: 200, data: { ok: true } };

            expect(interceptors.responseOnFulfilled!(response)).toBe(response);
        });

        it("rejects non-401 errors without touching auth store", async () => {
            primeHttp();
            const err = { response: { status: 500 } };

            await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);
            expect(mockStoreSignOut).not.toHaveBeenCalled();
            expect(Snackbar.show).not.toHaveBeenCalled();
        });

        it("rejects 401s on NO_FORCED_SIGNOUT paths without signing out", async () => {
            primeHttp();
            Object.defineProperty(window, "location", {
                configurable: true,
                value: { pathname: "/auth/mfa-verify" }
            });
            const err = { response: { status: 401 } };

            await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);
            expect(mockStoreSignOut).not.toHaveBeenCalled();
            expect(Snackbar.show).not.toHaveBeenCalled();
        });

        it("signs out and shows a snackbar on 401s from protected routes", async () => {
            primeHttp();
            Object.defineProperty(window, "location", {
                configurable: true,
                value: { pathname: "/projects" }
            });
            const err = { response: { status: 401 } };

            await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);

            expect(mockStoreSignOut).toHaveBeenCalledTimes(1);
            expect(Snackbar.show).toHaveBeenCalledWith({
                type: "info",
                title: "Not Authorised",
                text: "You have been signed out. Please log back in."
            });
        });

        it("debounces 'Not Authorised' snackbars when 401s arrive in a burst", async () => {
            primeHttp();
            Object.defineProperty(window, "location", {
                configurable: true,
                value: { pathname: "/projects" }
            });
            const err = { response: { status: 401 } };

            // The interceptor's cooldown is tracked in module-level state
            // that persists across tests. Jump far enough into the future
            // that the prior test's timestamp has fully expired, then keep
            // Date.now() fixed so all three 401s land inside one window.
            const spy = vi.spyOn(Date, "now").mockReturnValue(9_999_999_999_999);
            try {
                await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);
                await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);
                await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);

                expect(Snackbar.show).toHaveBeenCalledTimes(1);
                expect(mockStoreSignOut).toHaveBeenCalledTimes(3);
            } finally {
                spy.mockRestore();
            }
        });

        it("handles error objects with no response field", async () => {
            primeHttp();
            const err = { message: "network error" };

            await expect(interceptors.responseOnRejected!(err)).rejects.toBe(err);
            expect(mockStoreSignOut).not.toHaveBeenCalled();
        });
    });

    describe("proxy methods", () => {
        it("forwards get/post/put/delete to the underlying axios instance", async () => {
            fakeAxiosInstance.get.mockResolvedValue({ data: "g" });
            fakeAxiosInstance.post.mockResolvedValue({ data: "p" });
            fakeAxiosInstance.put.mockResolvedValue({ data: "u" });
            fakeAxiosInstance.delete.mockResolvedValue({ data: "d" });

            await _http.get("/a");
            await _http.post("/b", { x: 1 });
            await _http.put("/c", { y: 2 });
            await _http.delete("/d");

            expect(fakeAxiosInstance.get).toHaveBeenCalledWith("/a", undefined);
            expect(fakeAxiosInstance.post).toHaveBeenCalledWith("/b", { x: 1 }, undefined);
            expect(fakeAxiosInstance.put).toHaveBeenCalledWith("/c", { y: 2 }, undefined);
            expect(fakeAxiosInstance.delete).toHaveBeenCalledWith("/d", undefined);
        });

        it("reuses the same axios instance across calls", async () => {
            fakeAxiosInstance.get.mockResolvedValue({ data: "ok" });

            await _http.get("/a");
            await _http.get("/b");

            expect(vi.mocked(axios.create)).toHaveBeenCalledTimes(1);
        });
    });
});
