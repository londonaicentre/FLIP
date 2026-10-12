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

import { DEMO_CAPTURE_DATE, DEMO_EVALUATION_CAPTURE_DATE } from "../../../mocks/demo/ark-plus-register";
import DemoBanner from "../DemoBanner.vue";

const humanised = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC"
});

describe("DemoBanner", () => {
    it("renders a persistent, non-dismissible read-only-demo label", () => {
        const comp = mountComponent(DemoBanner);

        expect(comp.find("[data-test='demo-banner']").exists()).toBe(true);
        // No close/dismiss control anywhere — unlike AiBanner, this must stay
        // on screen for the whole session (see the template comment).
        expect(comp.find("button").exists()).toBe(false);
    });

    // Guard the inputs before anything is formatted from them. `humanised()`
    // below re-implements the component's own formatter, so on a malformed
    // constant both sides produce "Invalid Date" and every toContain agrees
    // with it. An ISO-shape check is what makes the assertions load-bearing.
    it("exposes both capture dates as ISO calendar dates", () => {
        for (const iso of [DEMO_CAPTURE_DATE, DEMO_EVALUATION_CAPTURE_DATE]) {
            expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
            expect(Number.isNaN(new Date(`${iso}T00:00:00Z`).getTime())).toBe(false);
        }
    });

    it("names BOTH capture dates, in order, human-readable", () => {
        const comp = mountComponent(DemoBanner);
        const text = comp.text().replace(/\s+/g, " ").trim();

        // The register spans two captures (the fine-tuning project was
        // re-captured alone after the FLIP#821 orientation fix). Showing only
        // one date silently misdates half the exhibit, which is exactly what
        // the register's provenance discipline exists to prevent.
        //
        // Assert the whole sentence rather than two independent substrings:
        // order-insensitive toContain calls pass just as happily with the two
        // dates swapped, which would misdate both halves at once.
        expect(text).toBe(
            "Read-only demo — snapshot of the Ark+ federated experiments: fine-tuning captured "
            + `${humanised(DEMO_CAPTURE_DATE)}, evaluation ${humanised(DEMO_EVALUATION_CAPTURE_DATE)}.`
        );

        // And pin the rendered shape independently of the formatter, so a
        // change that still round-trips through humanised() cannot pass.
        const DAY_MON_YEAR = String.raw`\d{2} [A-Z][a-z]{2} \d{4}`;
        expect(text).toMatch(new RegExp(
            String.raw`^Read-only demo — snapshot of the Ark\+ federated experiments: `
            + String.raw`fine-tuning captured ${DAY_MON_YEAR}, evaluation ${DAY_MON_YEAR}\.$`
        ));
    });
});
