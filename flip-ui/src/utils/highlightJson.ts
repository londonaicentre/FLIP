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

import { Directive } from "vue";

/**
 * Lazy JSON syntax-highlighting directive (#716 entry-chunk diet).
 *
 * Replaces the app-wide `vue3-highlightjs` plugin, which bundled the full
 * highlight.js (every language) into the entry chunk for a single consumer.
 * The slim core + JSON grammar load on demand the first time a `v-highlightjs`
 * element mounts, so routes that never open the FL-net details pay nothing.
 */
const highlight = async (el: HTMLElement): Promise<void> => {
    const [{ default: hljs }, { default: json }] = await Promise.all([
        import("highlight.js/lib/core"),
        import("highlight.js/lib/languages/json")
    ]);
    hljs.registerLanguage("json", json);
    el.querySelectorAll<HTMLElement>("pre code").forEach((block) => hljs.highlightElement(block));
};

export const vHighlightjs: Directive<HTMLElement> = {
    mounted: highlight,
    updated: highlight
};
