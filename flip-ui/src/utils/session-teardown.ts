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
 * Leaving a session means tearing the whole SPA down, not routing away from it.
 *
 * A signed-in session leaves data in module-level memory that no store reset reaches:
 * swrv's cache is a module global with `ttl` 0 (never expires), and every Pinia store
 * other than `auth` keeps whatever it last loaded. `signOut` used to `$reset()` the auth
 * store and `router.push("/auth/login")`, which left all of that intact, so the next
 * account signing in to the same tab was rendered the previous user's projects, models,
 * cohort results and metrics straight from cache — with `dedupingInterval` suppressing
 * the refetch that would have corrected it (FLIP#995). Scoping individual swrv keys to
 * the caller (`/users/me#<sub>`) closed one such key, but a constant key anywhere in the
 * tree re-opens it, and swrv's default cache instance is not exported to be cleared.
 *
 * The general fix is a hard navigation: `window.location.assign` discards the document
 * and its JavaScript heap, so there is nothing left to leak — no cache, no store, no
 * closure. Every path that ends a session goes through {@link leaveToLogin} so there is
 * one definition of "gone". The cost is that anything shown on the way out is discarded
 * with the page, so notices that must outlive it are handed across the reload through
 * `sessionStorage` (per tab, cleared with it, untouched by the `localStorage.clear()`
 * the route guard performs) and replayed once by `App.vue` on boot.
 */

import type { ISnackbar } from "@/components/AiSnackbar/notify";

import { Snackbar } from "./snackbar";

export const POST_SIGNOUT_NOTICE_KEY = "flip:post-signout-notice";
export const LOGIN_PATH = "/auth/login";

/** The subset of a snackbar that survives a reload: no callbacks, nothing non-serialisable. */
export type PostSignOutNotice = Required<Pick<ISnackbar, "type" | "title" | "text">>;

/**
 * Queue a notice to be shown once the login page has come back up after {@link leaveToLogin}.
 * Storage failures (private mode, quota) are swallowed: a lost notice must never block the
 * teardown that is the actual security control.
 */
export const stashPostSignOutNotice = (notice: PostSignOutNotice): void => {
    try {
        window.sessionStorage.setItem(POST_SIGNOUT_NOTICE_KEY, JSON.stringify(notice));
    } catch {
        // no-op: see above
    }
};

/**
 * Remove and return the queued notice, or `null`. One-shot: a reload of the login page
 * itself must not show the same notice twice, and a malformed entry is dropped rather than
 * thrown on, since it can only have come from this module.
 */
export const takePostSignOutNotice = (): PostSignOutNotice | null => {
    try {
        const raw = window.sessionStorage.getItem(POST_SIGNOUT_NOTICE_KEY);
        if (raw === null) {
            return null;
        }
        window.sessionStorage.removeItem(POST_SIGNOUT_NOTICE_KEY);
        const parsed: unknown = JSON.parse(raw);
        if (
            typeof parsed === "object" && parsed !== null
            && typeof (parsed as PostSignOutNotice).title === "string"
            && typeof (parsed as PostSignOutNotice).text === "string"
            && typeof (parsed as PostSignOutNotice).type === "string"
        ) {
            return parsed as PostSignOutNotice;
        }

        return null;
    } catch {
        return null;
    }
};

/**
 * Show the queued notice, if any, in the fresh document. Called once from `App.vue` on
 * boot; a normal boot finds nothing and does nothing. Returns whether one was shown.
 */
export const replayPostSignOutNotice = (): boolean => {
    const notice = takePostSignOutNotice();
    if (!notice) {
        return false;
    }
    (notice.type === "error" ? Snackbar.error : Snackbar.show)(notice);

    return true;
};

/**
 * Discard the running SPA and load the login page fresh. Not a router push: the point is
 * that no in-memory state survives. Callers that have a notice for the user stash it first.
 */
export const leaveToLogin = (): void => {
    window.location.assign(LOGIN_PATH);
};
