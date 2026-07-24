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

import { vHighlightjs } from "@/utils/highlightJson";

describe("highlightJson directive", () => {
    it("lazily highlights JSON code blocks in the bound element", async () => {
        const el = document.createElement("div");
        el.innerHTML = "<pre><code class=\"json\">{ \"net\": \"net-1\", \"online\": true }</code></pre>";

        // mounted returns the lazy-import promise, so tests can await the work.
        await (vHighlightjs.mounted as (element: HTMLElement) => Promise<void>)(el);

        // highlight.js wraps tokens in hljs-* spans once it has run.
        expect(el.querySelector("code")?.innerHTML).toContain("hljs-");
    });
});
