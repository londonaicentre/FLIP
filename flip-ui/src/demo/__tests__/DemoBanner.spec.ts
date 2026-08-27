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

import { mountComponent } from "@test/helper";

import { DEMO_CAPTURE_DATE } from "../../../mocks/demo/ark-plus-register";
import DemoBanner from "../DemoBanner.vue";

describe("DemoBanner", () => {
    it("renders a persistent, non-dismissible read-only-demo label", () => {
        const comp = mountComponent(DemoBanner);

        expect(comp.find("[data-test='demo-banner']").exists()).toBe(true);
        // No close/dismiss control anywhere — unlike AiBanner, this must stay
        // on screen for the whole session (see the template comment).
        expect(comp.find("button").exists()).toBe(false);
    });

    it("stamps the register's capture date, human-readable", () => {
        const comp = mountComponent(DemoBanner);

        // DEMO_CAPTURE_DATE is "YYYY-MM-DD"; assert against the same
        // formatting the component uses so this doesn't hardcode a date
        // string that drifts the moment the register is re-captured.
        const expected = new Date(`${DEMO_CAPTURE_DATE}T00:00:00Z`).toLocaleDateString("en-GB", {
            day: "2-digit",
            month: "short",
            year: "numeric",
            timeZone: "UTC"
        });

        expect(comp.text()).toContain(expected);
        expect(comp.text()).toContain("Read-only demo");
    });
});
