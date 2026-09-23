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

import { describe, expect, it } from "vitest";

import { decodeJwtPayload } from "@/auth/jwt";

// Build an unsigned JWT the way a test would: base64url segments, no padding.
function makeJwt(payload: Record<string, unknown>): string {
    const b64url = (s: string): string =>
        Buffer.from(s, "utf8").toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

    return `${b64url(JSON.stringify({
        alg: "RS256",
        typ: "JWT"
    }))}.${b64url(JSON.stringify(payload))}.sig`;
}

describe("decodeJwtPayload", () => {
    it("returns the payload claims of a base64url-encoded token", () => {
        const token = makeJwt({
            sub: "abc-123",
            email: "u@e.com",
            preferred_username: "u"
        });

        expect(decodeJwtPayload(token)).toEqual({
            sub: "abc-123",
            email: "u@e.com",
            preferred_username: "u"
        });
    });

    it("handles unpadded base64url with - and _ characters", () => {
        // A payload whose base64 contains + and / in standard encoding, so
        // the url-safe variant must be translated back before decoding.
        const payload = {
            name: "??>>??>>",
            n: 12345
        };
        const token = makeJwt(payload);

        expect(token).toMatch(/[-_]/);
        expect(decodeJwtPayload(token)).toEqual(payload);
    });

    it("decodes non-ASCII claims as UTF-8, not Latin-1", () => {
        const token = makeJwt({ name: "Zoë Ångström" });

        expect(decodeJwtPayload(token)).toEqual({ name: "Zoë Ångström" });
    });

    it("throws on a string that is not three dot-separated segments", () => {
        expect(() => decodeJwtPayload("not-a-jwt")).toThrow(/JWT/);
        expect(() => decodeJwtPayload("")).toThrow(/JWT/);
    });

    it("throws when the payload segment is not JSON", () => {
        expect(() => decodeJwtPayload("aGVhZGVy.bm90LWpzb24.sig")).toThrow();
    });
});
