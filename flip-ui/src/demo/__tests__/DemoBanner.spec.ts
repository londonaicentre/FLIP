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
import { vi } from "vitest";

import { EARLIEST_CAPTURE_DATE, LATEST_CAPTURE_DATE, PROJECT_COUNT } from "../../../mocks/demo/capture-window";
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
    it("exposes the capture window as ISO calendar dates", () => {
        for (const iso of [EARLIEST_CAPTURE_DATE, LATEST_CAPTURE_DATE]) {
            expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
            expect(Number.isNaN(new Date(`${iso}T00:00:00Z`).getTime())).toBe(false);
        }
    });

    it("names the capture window from the registry", () => {
        const comp = mountComponent(DemoBanner);
        const text = comp.text().replace(/\s+/g, " ").trim();

        // Assert the whole sentence: order-insensitive toContain calls pass
        // just as happily with the two dates swapped.
        expect(text).toBe(
            `Read-only demo of FLIP on example data — ${PROJECT_COUNT} federated projects, `
            + `snapshots captured between ${humanised(EARLIEST_CAPTURE_DATE)} and ${humanised(LATEST_CAPTURE_DATE)}.`
        );

        // And pin the rendered shape independently of the formatter, so a
        // change that still round-trips through humanised() cannot pass.
        const DAY_MON_YEAR = String.raw`\d{2} [A-Z][a-z]{2} \d{4}`;
        expect(text).toMatch(new RegExp(
            String.raw`^Read-only demo of FLIP on example data — \d+ federated projects, `
            + String.raw`snapshots captured between ${DAY_MON_YEAR} and ${DAY_MON_YEAR}\.$`
        ));
    });

    it("collapses to one date when every project shares it", async () => {
        vi.resetModules();
        vi.doMock("../../../mocks/demo/capture-window", () => ({
            PROJECT_COUNT: 2,
            EARLIEST_CAPTURE_DATE: "2026-11-02",
            LATEST_CAPTURE_DATE: "2026-11-02"
        }));
        const { default: Banner } = await import("../DemoBanner.vue");
        const text = mountComponent(Banner).text().replace(/\s+/g, " ").trim();
        expect(text).toBe(
            `Read-only demo of FLIP on example data — 2 federated projects, snapshots captured ${humanised("2026-11-02")}.`
        );
        vi.doUnmock("../../../mocks/demo/capture-window");
    });
});
