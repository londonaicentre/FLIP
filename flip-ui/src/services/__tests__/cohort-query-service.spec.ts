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

import { describe, expect, test, vi } from "vitest";

import { _http } from "@/services/api";
import { getOMOPResults } from "@/services/cohort-query-service";

vi.mock("@/services/api", () => {
    return { _http: { get: vi.fn() } };
});

describe("getOMOPResults", () => {
    test("returns parsed results on HTTP 200", async () => {
        const payload = { recordCount: 0, trustsResults: [], failures: [] };
        vi.mocked(_http.get).mockResolvedValueOnce({ status: 200, data: payload });

        const result = await getOMOPResults("/cohort/abc");

        expect(result).toEqual(payload);
    });

    test("surfaces per-trust failures alongside results", async () => {
        // Regression guard for the bug where a query that fails at every trust would leave the
        // UI spinning forever. The hub now returns 200 with an empty trustsResults and a
        // populated failures list, which the service must pass through unmodified — including
        // the categorised ``reason`` so the UI can render cohort-size failures distinctly from
        // generic infra failures.
        const payload = {
            recordCount: 0,
            trustsResults: [],
            failures: [
                {
                    trustName: "Trust A",
                    message: "Cohort below minimum size threshold (0 record(s) matched, 5 required).",
                    reason: "insufficient_cohort",
                },
                { trustName: "Trust B", message: "Connection refused", reason: "other" },
            ],
        };
        vi.mocked(_http.get).mockResolvedValueOnce({ status: 200, data: payload });

        const result = await getOMOPResults("/cohort/abc");

        expect(result).toEqual(payload);
        expect(result?.failures).toHaveLength(2);
        expect(result?.failures?.[0].reason).toBe("insufficient_cohort");
    });

    test("returns null on HTTP 202 so SWRV keeps polling without logging an error", async () => {
        vi.mocked(_http.get).mockResolvedValueOnce({ status: 202, data: { status: "pending" } });

        const result = await getOMOPResults("/cohort/abc");

        expect(result).toBeNull();
    });

    test("accepts 202 as non-error via validateStatus override", async () => {
        // Regression guard: without validateStatus including 202, axios would throw and surface
        // a noisy console error to the user while the UI polls for a freshly-submitted query.
        vi.mocked(_http.get).mockResolvedValueOnce({ status: 202, data: { status: "pending" } });

        await getOMOPResults("/cohort/abc");

        const [, config] = vi.mocked(_http.get).mock.calls[0];
        expect(config?.validateStatus?.(202)).toBe(true);
        expect(config?.validateStatus?.(200)).toBe(true);
        expect(config?.validateStatus?.(404)).toBe(false);
    });
});
