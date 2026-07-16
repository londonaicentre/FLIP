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



import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const indexHtml = readFileSync(resolve(__dirname, "../../index.html"), "utf-8");

describe("index.html document metas", () => {
    it("declares viewport-fit=cover so iOS lets the page (and corner artwork) reach the screen edges", () => {
        // Without viewport-fit=cover, iOS letterboxes the layout viewport inside the safe area:
        // the corner artwork anchored at top-0/bottom-0 stops short of the physical screen edges
        // and its bleed-cropped cut edges show as floating straight lines (#791).
        const viewport = indexHtml.match(/<meta name="viewport" content="([^"]*)"/);

        expect(viewport?.[1]).toContain("viewport-fit=cover");
        expect(viewport?.[1]).toContain("width=device-width");
    });

    it("ships a pre-hydration theme-color for browsers that honour it", () => {
        expect(indexHtml).toMatch(/<meta name="theme-color" content="#FAF7F5"/);
    });
});
