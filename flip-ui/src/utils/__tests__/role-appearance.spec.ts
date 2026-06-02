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

import { ROLE_TONES, roleTone, roleToneName, userInitials } from "@/utils/role-appearance";

describe("ROLE_TONES", () => {
    it("defines the five handoff tones with fg/bg/ring hex values", () => {
        expect(Object.keys(ROLE_TONES).sort()).toEqual(["gray", "magenta", "navy", "purple", "steel"]);
        for (const tone of Object.values(ROLE_TONES)) {
            expect(tone.fg).toMatch(/^#[0-9A-F]{6}$/i);
            expect(tone.bg).toMatch(/^#[0-9A-F]{6}$/i);
            expect(tone.ring).toMatch(/^#[0-9A-F]{6}$/i);
        }
    });
});

describe("roleToneName", () => {
    it("maps FLIP's three real roles to their handoff tones", () => {
        expect(roleToneName("Admin")).toBe("magenta");
        expect(roleToneName("Researcher")).toBe("steel");
        expect(roleToneName("Observer")).toBe("gray");
    });

    it("is case-insensitive and tolerant of surrounding whitespace", () => {
        expect(roleToneName("admin")).toBe("magenta");
        expect(roleToneName("  RESEARCHER ")).toBe("steel");
    });

    it("falls back to gray for an empty or missing role name", () => {
        expect(roleToneName("")).toBe("gray");
        expect(roleToneName(undefined)).toBe("gray");
        expect(roleToneName(null)).toBe("gray");
    });

    it("assigns unknown roles a stable, deterministic tone", () => {
        const first = roleToneName("Data Steward");
        const second = roleToneName("Data Steward");
        expect(first).toBe(second);
        expect(Object.keys(ROLE_TONES)).toContain(first);
    });
});

describe("roleTone", () => {
    it("returns the tone object for the resolved tone name", () => {
        expect(roleTone("Admin")).toEqual(ROLE_TONES.magenta);
        expect(roleTone(undefined)).toEqual(ROLE_TONES.gray);
    });
});

describe("userInitials", () => {
    it("takes the first and last initial of a name", () => {
        expect(userInitials("Priya Raghavan", "p.raghavan@kcl.ac.uk")).toBe("PR");
    });

    it("strips honorifics before deriving initials", () => {
        expect(userInitials("Dr Amara Okonkwo", "a.okonkwo@gstt.nhs.uk")).toBe("AO");
        expect(userInitials("Prof. Mei-Lin Chen", "ml.chen@gstt.nhs.uk")).toBe("MC");
    });

    it("handles a single-word name", () => {
        expect(userInitials("Cher", "cher@example.com")).toBe("C");
    });

    it("falls back to the email local part when no usable name is present", () => {
        expect(userInitials("", "p.raghavan@kcl.ac.uk")).toBe("PR");
        expect(userInitials("Dr", "tomas.ribeiro@nhs.uk")).toBe("TR");
    });

    it("returns a placeholder when neither name nor email is usable", () => {
        expect(userInitials("", "")).toBe("?");
        expect(userInitials(undefined, undefined)).toBe("?");
    });
});
