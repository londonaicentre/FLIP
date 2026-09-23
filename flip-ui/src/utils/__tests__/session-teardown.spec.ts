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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { leaveToLogin,
    LOGIN_PATH,
    POST_SIGNOUT_NOTICE_KEY,
    replayPostSignOutNotice,
    stashPostSignOutNotice,
    takePostSignOutNotice } from "@/utils/session-teardown";
import { Snackbar } from "@/utils/snackbar";

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));

const notice = {
    type: "error" as const,
    title: "Sign-out incomplete",
    text: "Close all windows."
};

describe("session-teardown", () => {
    beforeEach(() => {
        window.sessionStorage.clear();
        vi.mocked(Snackbar.error).mockReset();
        vi.mocked(Snackbar.show).mockReset();
    });

    afterEach(() => {
        window.sessionStorage.clear();
    });

    describe("leaveToLogin", () => {
        it("discards the document with a hard navigation to the login page, not a router push", () => {
            // jsdom does not implement navigation, so replace `location` with a spy-bearing
            // stand-in for this test only.
            const original = window.location;
            const assign = vi.fn();
            Object.defineProperty(window, "location", {
                configurable: true,
                value: { assign }
            });
            try {
                leaveToLogin();
                expect(assign).toHaveBeenCalledTimes(1);
                expect(assign).toHaveBeenCalledWith(LOGIN_PATH);
                expect(LOGIN_PATH).toBe("/auth/login");
            } finally {
                Object.defineProperty(window, "location", {
                    configurable: true,
                    value: original
                });
            }
        });
    });

    describe("stash / take", () => {
        it("round-trips a notice through sessionStorage, untouched by localStorage.clear()", () => {
            stashPostSignOutNotice(notice);
            // The route guard clears localStorage on its way out; the queued notice must survive that.
            window.localStorage.clear();
            expect(takePostSignOutNotice()).toEqual(notice);
        });

        it("is one-shot: the same notice is never shown twice", () => {
            stashPostSignOutNotice(notice);
            expect(takePostSignOutNotice()).toEqual(notice);
            expect(takePostSignOutNotice()).toBeNull();
            expect(window.sessionStorage.getItem(POST_SIGNOUT_NOTICE_KEY)).toBeNull();
        });

        it("returns null when nothing is queued", () => {
            expect(takePostSignOutNotice()).toBeNull();
        });

        it("drops, rather than throws on, a malformed or partial entry", () => {
            window.sessionStorage.setItem(POST_SIGNOUT_NOTICE_KEY, "{not json");
            expect(takePostSignOutNotice()).toBeNull();
            window.sessionStorage.setItem(POST_SIGNOUT_NOTICE_KEY, JSON.stringify({ title: "no text or type" }));
            expect(takePostSignOutNotice()).toBeNull();
            expect(window.sessionStorage.getItem(POST_SIGNOUT_NOTICE_KEY)).toBeNull();
        });

        it("never lets a storage failure block the teardown", () => {
            const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
                throw new DOMException("QuotaExceededError");
            });
            try {
                expect(() => stashPostSignOutNotice(notice)).not.toThrow();
            } finally {
                setItem.mockRestore();
            }
        });
    });

    describe("replayPostSignOutNotice", () => {
        it("shows an error notice through Snackbar.error, once, and reports it did", () => {
            stashPostSignOutNotice(notice);
            expect(replayPostSignOutNotice()).toBe(true);
            expect(Snackbar.error).toHaveBeenCalledWith(notice);
            expect(Snackbar.show).not.toHaveBeenCalled();
            // A second boot (e.g. the user reloads the login page) shows nothing.
            expect(replayPostSignOutNotice()).toBe(false);
            expect(Snackbar.error).toHaveBeenCalledTimes(1);
        });

        it("shows a non-error notice through Snackbar.show", () => {
            const info = {
                type: "info" as const,
                title: "Session already ended",
                text: "Signed out."
            };
            stashPostSignOutNotice(info);
            expect(replayPostSignOutNotice()).toBe(true);
            expect(Snackbar.show).toHaveBeenCalledWith(info);
            expect(Snackbar.error).not.toHaveBeenCalled();
        });

        it("is a no-op on a normal boot", () => {
            expect(replayPostSignOutNotice()).toBe(false);
            expect(Snackbar.show).not.toHaveBeenCalled();
            expect(Snackbar.error).not.toHaveBeenCalled();
        });
    });
});
