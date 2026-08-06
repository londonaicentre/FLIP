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
import { describe, expect, test } from "vitest";

import { IStep } from "@/interfaces/steps";

import LifecycleTrack from "../LifecycleTrack.vue";

describe("LifecycleTrack", () => {
    // completed → current (first non-completed) → error → stopped → future:
    // hits every circleClass()/stepTextClass() branch, including the dark-mode
    // "current" and "default" returns.
    const steps: IStep[] = [
        {
            id: "1",
            name: "Created",
            completed: true,
            date: "2026-01-01"
        },
        {
            id: "2",
            name: "Staging"
        },
        {
            id: "3",
            name: "Training",
            error: true
        },
        {
            id: "4",
            name: "Halted",
            stopped: true
        },
        {
            id: "5",
            name: "Done"
        }
    ];

    test("renders one node per step", () => {
        const comp = mount(LifecycleTrack, { props: { steps } });

        expect(comp.findAll("[role='listitem']")).toHaveLength(steps.length);
    });

    test("labels each step and shows the current stage as in progress", () => {
        const comp = mount(LifecycleTrack, { props: { steps } });

        expect(comp.text()).toContain("Created");
        expect(comp.text()).toContain("Done");
        expect(comp.text()).toContain("in progress");
    });
});
