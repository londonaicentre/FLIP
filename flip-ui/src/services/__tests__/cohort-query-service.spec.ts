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
import { getOMOPResults, sendQuery } from "@/services/cohort-query-service";

vi.mock("@/services/api", () => {
    return {
        _http: {
            get: vi.fn(),
            post: vi.fn()
        }
    };
});

describe("getOMOPResults", () => {
    test("returns parsed results on HTTP 200", async () => {
        const payload = {
            name: "X",
            results: []
        };
        vi.mocked(_http.get).mockResolvedValueOnce({
            status: 200,
            data: payload
        });

        const result = await getOMOPResults("/cohort/abc");

        expect(result).toEqual(payload);
    });

    test("returns null on HTTP 202 so SWRV keeps polling without logging an error", async () => {
        vi.mocked(_http.get).mockResolvedValueOnce({
            status: 202,
            data: { status: "pending" }
        });

        const result = await getOMOPResults("/cohort/abc");

        expect(result).toBeNull();
    });

    test("accepts 202 as non-error via validateStatus override", async () => {
        // Regression guard: without validateStatus including 202, axios would throw and surface
        // a noisy console error to the user while the UI polls for a freshly-submitted query.
        vi.mocked(_http.get).mockResolvedValueOnce({
            status: 202,
            data: { status: "pending" }
        });

        await getOMOPResults("/cohort/abc");

        const [, config] = vi.mocked(_http.get).mock.calls[0];
        expect(config?.validateStatus?.(202)).toBe(true);
        expect(config?.validateStatus?.(200)).toBe(true);
        expect(config?.validateStatus?.(404)).toBe(false);
    });
});

describe("sendQuery", () => {
    test("POSTs the cohort query and returns the hub's per-trust dispatch result", async () => {
        const body = {
            name: "q",
            projectId: "p-1",
            query: "SELECT 1"
        };
        const response = {
            queryId: "q-1",
            trust: [{
                name: "Trust_1",
                statusCode: 200
            }]
        };
        vi.mocked(_http.post).mockResolvedValueOnce({ data: response });

        const result = await sendQuery("/cohort", body);

        expect(_http.post).toHaveBeenCalledWith("/cohort", body);
        expect(result).toEqual(response);
    });
});
