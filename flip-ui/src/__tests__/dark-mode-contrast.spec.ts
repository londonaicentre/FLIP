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

// Source-level contrast guard (#716): `dark:text-gray-500` reads at ~3.7:1 on
// the dark surfaces — below the 4.5:1 WCAG AA floor for small text. Muted
// dark-mode text belongs on gray-400 (the dark fg-3 token in
// tailwind.config.js); gray-500 is reserved for genuinely disabled controls.
const sources = import.meta.glob("../**/*.vue", {
    query: "?raw",
    import: "default",
    eager: true
}) as Record<string, string>;

describe("dark-mode contrast guard", () => {
    it("keeps dark-mode text off text-gray-500 (any variant chain)", () => {
        const offenders = Object.entries(sources)
            .filter(([, code]) => /dark:(?:[a-z-]+:)*text-gray-500/.test(code))
            .map(([file]) => file);

        expect(offenders).toEqual([]);
    });
});
