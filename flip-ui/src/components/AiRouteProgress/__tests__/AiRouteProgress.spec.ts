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

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { nextTick } from "vue";

import { routeProgress } from "@/router/progress";

import AiRouteProgress from "../AiRouteProgress.vue";

describe("AiRouteProgress", () => {
    afterEach(() => {
        routeProgress.visible = false;
        routeProgress.width = 0;
    });

    it("stays hidden, and out of the accessibility tree, while no navigation is in flight", () => {
        const bar = mount(AiRouteProgress).find("[data-test=route-progress]");

        expect(bar.isVisible()).toBe(false);
        expect(bar.attributes("aria-hidden")).toBe("true");
    });

    it("shows the bar at the router's progress width", async () => {
        const wrapper = mount(AiRouteProgress, { attachTo: document.body });

        routeProgress.visible = true;
        routeProgress.width = 42;
        await nextTick();

        const bar = wrapper.find("[data-test=route-progress]");
        expect(bar.isVisible()).toBe(true);
        expect((bar.element as HTMLElement).style.width).toBe("42%");
        wrapper.unmount();
    });
});
