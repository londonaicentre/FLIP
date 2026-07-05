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

// Source-level contrast guard (#716): the dark-mode ink floor is gray-300.
// gray-500 reads at ~3.7:1 on the dark surfaces (below the 4.5:1 WCAG AA
// floor for small text) and gray-400 was judged too dim in review, so muted
// dark-mode text sits on gray-300 and hover states lighten to gray-200.
const sources = import.meta.glob("../**/*.vue", {
    query: "?raw",
    import: "default",
    eager: true
}) as Record<string, string>;

describe("dark-mode contrast guard", () => {
    it("keeps dark-mode text at gray-300 or lighter (any variant chain)", () => {
        const offenders = Object.entries(sources)
            .filter(([, code]) => /dark:(?:[a-z-]+:)*text-gray-[45]00/.test(code))
            .map(([file]) => file);

        expect(offenders).toEqual([]);
    });

    it("pairs every light-mode gray text utility with an explicit dark ink", () => {
        // A bare `text-gray-400..900` with no `dark:text-*` on the same line
        // renders the light-mode gray onto the dark surfaces (invisible ink).
        // Variant-prefixed forms (hover:, placeholder:, dark: itself) are the
        // preceding rule's business, so only the bare utility is matched here.
        const offenders = Object.entries(sources).flatMap(([file, code]) =>
            code.split("\n")
                .map((line, idx) => ({
                    line,
                    at: `${file}:${idx + 1}`
                }))
                .filter(({ line }) => /["'\s{[(]text-gray-[4-9]00/.test(line) && !/dark:text-/.test(line))
                .map(({ at }) => at));

        expect(offenders).toEqual([]);
    });
});
