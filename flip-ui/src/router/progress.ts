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

import { reactive } from "vue";

/**
 * Top-of-page navigation progress bar state.
 *
 * With lazy route loading (`importMode: "async"`), navigating to a route the
 * user hasn't visited triggers a dynamic-import chunk fetch. While that is in
 * flight the previous view stays mounted, so without feedback a slow chunk
 * load looks like an unresponsive click. This drives a thin progress bar
 * rendered by `AiRouteProgress.vue`, started/finished from the router guards.
 *
 * A short start delay means navigations that resolve instantly — a cached
 * chunk, or staying on the same route — never flash the bar.
 */
const DELAY_MS = 150;
const CREEP_MS = 200;
const FINISH_MS = 200;

export const routeProgress = reactive({
    visible: false,
    width: 0
});

let delayTimer: ReturnType<typeof setTimeout> | undefined;
let creepTimer: ReturnType<typeof setInterval> | undefined;
let finishTimer: ReturnType<typeof setTimeout> | undefined;

/** Begin a navigation: after a short delay, reveal the bar and creep it toward 90%. */
export function startRouteProgress(): void {
    clearTimeout(delayTimer);
    clearTimeout(finishTimer);
    delayTimer = setTimeout(() => {
        routeProgress.visible = true;
        routeProgress.width = 8;
        creepTimer = setInterval(() => {
            // Ease toward 90% while the chunk is in flight; never reach 100%
            // until the navigation actually completes.
            routeProgress.width = Math.min(routeProgress.width + (90 - routeProgress.width) * 0.1, 90);
        }, CREEP_MS);
    }, DELAY_MS);
}

/** Finish a navigation: complete the bar to 100%, then hide it. No-op if never shown. */
export function doneRouteProgress(): void {
    clearTimeout(delayTimer);
    clearInterval(creepTimer);
    if (!routeProgress.visible) {
        routeProgress.width = 0;

        return;
    }
    routeProgress.width = 100;
    finishTimer = setTimeout(() => {
        routeProgress.visible = false;
        routeProgress.width = 0;
    }, FINISH_MS);
}
