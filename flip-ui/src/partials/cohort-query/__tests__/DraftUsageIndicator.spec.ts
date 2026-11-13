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

import DraftUsageIndicator from "../DraftUsageIndicator.vue";

const mountIndicator = (used: number) => mount(DraftUsageIndicator, {
    props: {
        usage: {
            limit: 30,
            used,
            resetsAt: "2026-12-01T00:00:00.000Z"
        }
    }
});

describe("DraftUsageIndicator", () => {
    it("is a focusable button whose label gives remaining, total and reset date", () => {
        const button = mountIndicator(12).get("[data-test='draft-usage-indicator']");

        expect(button.element.tagName).toBe("BUTTON");
        expect(button.attributes("type")).toBe("button");
        expect(button.attributes("aria-label")).toBe("18 of 30 drafts remaining. Resets 1 December.");
    });

    it("prints the remaining count, so the state does not rest on colour alone", () => {
        expect(mountIndicator(12).text()).toBe("18");
        expect(mountIndicator(30).text()).toBe("0");
        expect(mountIndicator(30).attributes("aria-label")).toBe("0 of 30 drafts remaining. Resets 1 December.");
    });

    it("fills the ring in proportion to the drafts used", () => {
        const dash = (used: number) => Number(
            mountIndicator(used).findAll("circle")[1].attributes("stroke-dasharray")?.split(" ")[0]
        );

        expect(dash(0)).toBe(0);
        expect(dash(15)).toBeCloseTo(Math.PI * 15);
        expect(dash(30)).toBeCloseTo(2 * Math.PI * 15);
    });
});
