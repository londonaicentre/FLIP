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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { doneRouteProgress, routeProgress, startRouteProgress } from "@/router/progress";

describe("route progress", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        // reset shared state between tests
        doneRouteProgress();
        vi.runAllTimers();
        routeProgress.visible = false;
        routeProgress.width = 0;
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("does not flash for navigations that resolve within the start delay", () => {
        startRouteProgress();
        // resolve before the 150ms reveal delay
        vi.advanceTimersByTime(100);
        doneRouteProgress();

        expect(routeProgress.visible).toBe(false);
        expect(routeProgress.width).toBe(0);
    });

    it("reveals and creeps the bar while a slow navigation is in flight", () => {
        startRouteProgress();
        vi.advanceTimersByTime(150); // reveal
        expect(routeProgress.visible).toBe(true);
        expect(routeProgress.width).toBe(8);

        const before = routeProgress.width;
        vi.advanceTimersByTime(200); // one creep tick
        expect(routeProgress.width).toBeGreaterThan(before);
        expect(routeProgress.width).toBeLessThanOrEqual(90);
    });

    it("completes then hides the bar when navigation finishes", () => {
        startRouteProgress();
        vi.advanceTimersByTime(150);
        expect(routeProgress.visible).toBe(true);

        doneRouteProgress();
        expect(routeProgress.width).toBe(100);

        vi.advanceTimersByTime(200); // finish delay
        expect(routeProgress.visible).toBe(false);
        expect(routeProgress.width).toBe(0);
    });
});
