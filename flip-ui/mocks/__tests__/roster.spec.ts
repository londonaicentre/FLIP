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

import { DEMO_NET_NAME, DEMO_SITES, DEMO_USER } from "../demo/roster";

describe("demo roster", () => {
    it("has exactly the two partner sites by name with demo ids", () => {
        expect(DEMO_SITES.map(site => site.code)).toEqual(["GSTT", "KCH"]);
        expect(DEMO_SITES.map(site => site.name)).toEqual([
            "Guy's and St Thomas' Trust",
            "King's College Hospital"
        ]);
        for (const site of DEMO_SITES) {
            expect(site.id).toMatch(/^00000000-0000-4000-8000-/);
            expect(site.region).toBe("London");
        }
        expect(new Set(DEMO_SITES.map(site => site.id)).size).toBe(2);
    });

    it("keeps the demo user id the artefact guard greps for", () => {
        expect(DEMO_USER.id).toBe("00000000-0000-4000-8000-000000000001");
        expect(DEMO_USER.email).toBe("demo@flip.local");
    });

    it("names one FL net", () => {
        expect(DEMO_NET_NAME).toBe("net-1");
    });
});
