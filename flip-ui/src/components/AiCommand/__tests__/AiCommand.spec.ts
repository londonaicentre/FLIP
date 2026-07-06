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

import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, test } from "vitest";

import AiCommand from "../AiCommand.vue";

describe("Ai Command", () => {
    test("names the icon-only close button for assistive tech", async () => {
        // HeadlessUI's Dialog is stubbed in the global test setup (jsdom has
        // no focus trap) and teleports content out of the SFC tree — render
        // the stub's slot and query document.body for the panel content.
        const wrapper = mount(AiCommand, {
            attachTo: document.body,
            props: { open: true },
            global: { renderStubDefaultSlot: true }
        });
        await flushPromises();

        const closeButton = document.body.querySelector("button[aria-label='Close']");
        expect(closeButton).not.toBeNull();
        wrapper.unmount();
    });
});
