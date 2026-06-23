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

import { beforeEach, describe, expect, it, vi } from "vitest";

import { _http } from "@/services/api";
import { getTrustStatuses } from "@/services/trust-status-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("trust-status-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
    });

    describe("getTrustStatuses", () => {
        it("GETs the non-admin /trust/status endpoint and returns the array", async () => {
            // Regression for #557: the Connection Status page must source its
            // data from an authenticated (non-admin) endpoint so Researcher /
            // Viewer roles don't get a 403 and a perpetual spinner.
            const statuses = [
                {
                    id: "t-1",
                    name: "Trust_1",
                    code: "T1",
                    region: "London",
                    last_heartbeat: null,
                    project_count: 2
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: statuses } as never);

            const result = await getTrustStatuses();

            expect(_http.get).toHaveBeenCalledWith("/trust/status");
            expect(result).toEqual(statuses);
        });

        it("returns [] when the backend body is not an array", async () => {
            // Defensive: the page drives a v-for off this list, so a non-array
            // body must not crash the table / topology render.
            vi.mocked(_http.get).mockResolvedValue({ data: { error: "oops" } } as never);

            const result = await getTrustStatuses();

            expect(result).toEqual([]);
        });

        it("propagates errors so SWRV keeps the loader instead of showing 'no trusts'", async () => {
            // A genuine fetch failure (e.g. 500) must reject — SWRV then leaves
            // `trusts` undefined and the page shows its loader, rather than
            // misreporting an empty federation.
            vi.mocked(_http.get).mockRejectedValue(new Error("Network Error"));

            await expect(getTrustStatuses()).rejects.toThrow("Network Error");
        });
    });
});
