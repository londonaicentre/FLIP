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

import { _http } from "@/services/api";
import { getMfaStatus,
    getUserPermissions,
    getUsers,
    registerUser,
    resetUserMfa,
    revokeToken,
    submitAccessRequest,
    updateUserDisabledState,
    updateUserRoles,
    validateUser } from "@/services/user-service";
import { Snackbar } from "@/utils/snackbar";

// Stub the low-level axios wrapper so each test can assert the URL + payload
// without spinning up a real HTTP client.
vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));

describe("user-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
        vi.mocked(_http.post).mockReset();
        vi.mocked(_http.put).mockReset();
        vi.mocked(_http.delete).mockReset();
        vi.mocked(Snackbar.error).mockReset();
    });

    describe("resetUserMfa", () => {
        it("POSTs to /users/{userId}/mfa/reset with no body", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await resetUserMfa("abc-123");

            expect(_http.post).toHaveBeenCalledTimes(1);
            expect(_http.post).toHaveBeenCalledWith("/users/abc-123/mfa/reset");
        });

        it("propagates the underlying error", async () => {
            // The admin users page relies on this throwing so the snackbar
            // shows a failure message — catching here would silently look
            // like success to the caller.
            vi.mocked(_http.post).mockRejectedValue(new Error("403 Forbidden"));

            await expect(resetUserMfa("abc-123")).rejects.toThrow("403 Forbidden");
        });
    });

    describe("getMfaStatus", () => {
        it("GETs /users/me/mfa/status and unwraps response.data", async () => {
            // The backend returns both enabled (is a TOTP device active?) and
            // required (does this environment demand MFA?) — the store
            // relies on both fields being passed through untouched.
            vi.mocked(_http.get).mockResolvedValue({
                data: {
                    enabled: true,
                    required: true
                }
            } as never);

            const result = await getMfaStatus();

            expect(_http.get).toHaveBeenCalledWith("/users/me/mfa/status");
            expect(result).toEqual({
                enabled: true,
                required: true
            });
        });

        it("returns enabled=false, required=false for an unenrolled dev caller", async () => {
            vi.mocked(_http.get).mockResolvedValue({
                data: {
                    enabled: false,
                    required: false
                }
            } as never);

            const result = await getMfaStatus();

            expect(result).toEqual({
                enabled: false,
                required: false
            });
        });
    });

    describe("getUserPermissions", () => {
        it("returns the permission list on success", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: { permissions: ["CanManageUsers"] } } as never);

            const result = await getUserPermissions("abc");

            expect(_http.get).toHaveBeenCalledWith("/users/abc/permissions");
            expect(result).toEqual({ permissions: ["CanManageUsers"] });
            expect(Snackbar.error).not.toHaveBeenCalled();
        });

        it("surfaces a snackbar and returns an empty list on a 5xx error", async () => {
            // 5xx (and network failures) are exactly the cases the user
            // needs to be told about — silent failure leaves them with a
            // half-broken UI and no recovery path.
            vi.mocked(_http.get).mockRejectedValue({ response: { status: 500 } });
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            const result = await getUserPermissions("abc");

            expect(result).toEqual({ permissions: [] });
            expect(Snackbar.error).toHaveBeenCalledTimes(1);
            expect(Snackbar.error).toHaveBeenCalledWith(expect.objectContaining({ title: expect.stringContaining("permissions") }));
            expect(consoleError).toHaveBeenCalled();
            consoleError.mockRestore();
        });

        it("surfaces a snackbar on a network error (no response)", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("Network Error"));
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            const result = await getUserPermissions("abc");

            expect(result).toEqual({ permissions: [] });
            expect(Snackbar.error).toHaveBeenCalledTimes(1);
            consoleError.mockRestore();
        });

        it("returns empty list silently on 401 (interceptor handles snackbar + sign-out)", async () => {
            // The api.ts response interceptor already shows a "Not
            // Authorised" snackbar and forces sign-out on 401; a second
            // snackbar here would stack messages, and rethrowing would
            // fail the surrounding hydrate / Promise.all chain in the
            // store with a 401 the user has already been told about.
            vi.mocked(_http.get).mockRejectedValue({ response: { status: 401 } });

            const result = await getUserPermissions("abc");

            expect(result).toEqual({ permissions: [] });
            expect(Snackbar.error).not.toHaveBeenCalled();
        });
    });

    describe("getUsers", () => {
        it("GETs the URL and returns the paginated body", async () => {
            const body = {
                page: 1,
                pageSize: 10,
                totalPages: 1,
                totalRecords: 0,
                data: []
            };
            vi.mocked(_http.get).mockResolvedValue({ data: body } as never);

            const result = await getUsers("/users?page=1");

            expect(_http.get).toHaveBeenCalledWith("/users?page=1");
            expect(result).toEqual(body);
        });
    });

    describe("updateUserRoles", () => {
        it("POSTs the role list under the `roles` key", async () => {
            // Pinned to `{ roles: [...] }` because the backend ignores any
            // top-level array body — a regression here would silently
            // succeed with no roles attached.
            vi.mocked(_http.post).mockResolvedValue({ data: ["r-1"] } as never);

            const result = await updateUserRoles("u-1", ["r-1"]);

            expect(_http.post).toHaveBeenCalledWith("/users/u-1/roles", { roles: ["r-1"] });
            expect(result).toEqual(["r-1"]);
        });
    });

    describe("registerUser", () => {
        it("POSTs to /step/users with the payload", async () => {
            const dto = {
                email: "new@example.test",
                roles: ["r-1"]
            };
            vi.mocked(_http.post).mockResolvedValue({ data: dto } as never);

            const result = await registerUser(dto);

            expect(_http.post).toHaveBeenCalledWith("/step/users", dto);
            expect(result).toEqual(dto);
        });
    });

    describe("updateUserDisabledState", () => {
        it("PUTs the disabled flag to /users/{userId}", async () => {
            vi.mocked(_http.put).mockResolvedValue({ data: { disabled: true } } as never);

            const result = await updateUserDisabledState("u-1", { disabled: true });

            expect(_http.put).toHaveBeenCalledWith("/users/u-1", { disabled: true });
            expect(result).toEqual({ disabled: true });
        });
    });

    describe("validateUser", () => {
        it("GETs /users/{email} and returns the resolved user", async () => {
            const user = {
                id: "u-1",
                email: "x@example.test",
                isDisabled: false
            };
            vi.mocked(_http.get).mockResolvedValue({ data: user } as never);

            const result = await validateUser("x@example.test");

            expect(_http.get).toHaveBeenCalledWith("/users/x@example.test");
            expect(result).toEqual(user);
        });
    });

    describe("revokeToken", () => {
        it("PUTs to /users/revoke/{refreshToken}", async () => {
            vi.mocked(_http.put).mockResolvedValue({ data: undefined } as never);

            await revokeToken("rt-abc");

            expect(_http.put).toHaveBeenCalledWith("/users/revoke/rt-abc");
        });
    });

    describe("submitAccessRequest", () => {
        it("POSTs to /users/access with an empty Authorization header (anonymous endpoint)", async () => {
            // The access-request form is reachable while signed out; the
            // empty Authorization tells the request interceptor to skip
            // injecting a Bearer token (which would 401 against a public
            // endpoint that rejects unrecognised JWTs).
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            const body = {
                email: "x@example.test",
                fullName: "X Y",
                reasonForAccess: "research"
            };

            await submitAccessRequest(body);

            expect(_http.post).toHaveBeenCalledWith(
                "/users/access",
                body,
                { headers: { Authorization: "" } }
            );
        });
    });
});
