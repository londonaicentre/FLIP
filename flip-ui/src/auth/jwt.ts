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

/**
 * Decode the payload (claims) segment of a JWT WITHOUT verifying it.
 *
 * The UI only needs the claims for display and for the flip-api user id; the
 * signature is checked where it matters — by flip-api on every request. Never
 * make an authorisation decision on what this returns.
 */
export function decodeJwtPayload(token: string): Record<string, unknown> {
    const segments = token.split(".");
    if (segments.length !== 3) {
        throw new Error("Not a JWT: expected three dot-separated segments");
    }
    // base64url -> base64, then restore the padding the JWT spec strips.
    const b64 = segments[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    // atob yields a Latin-1 byte string; re-read those bytes as UTF-8 so
    // non-ASCII names survive (`decodeURIComponent` + percent-escaping is the
    // dependency-free way to do that in every browser and in jsdom).
    const bytes = atob(padded);
    const json = decodeURIComponent(
        bytes.split("").map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")
    );

    return JSON.parse(json) as Record<string, unknown>;
}
