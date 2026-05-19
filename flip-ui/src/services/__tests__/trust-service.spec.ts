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
import { getTrusts } from "@/services/trust-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("trust-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
    });

    describe("getTrusts", () => {
        it("GETs /trust and returns the trust array on success", async () => {
            const trusts = [
                {
                    id: "t-1",
                    name: "Trust_1"
                },
                {
                    id: "t-2",
                    name: "Trust_2"
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: trusts } as never);

            const result = await getTrusts();

            expect(_http.get).toHaveBeenCalledWith("/trust");
            expect(result).toEqual(trusts);
        });

        it("returns [] when the backend body is not an array", async () => {
            // Defensive: the consuming pages drive a v-for off this list,
            // so a non-array body (e.g. an error object the backend
            // returned with 200) would crash the trust selector.
            vi.mocked(_http.get).mockResolvedValue({ data: { error: "oops" } } as never);

            const result = await getTrusts();

            expect(result).toEqual([]);
        });

        it("returns [] silently on network failure", async () => {
            // Trust list failure is not actionable for the user — the
            // surrounding form already handles "no trusts available" by
            // disabling the submit button. Throwing would bubble up to a
            // generic SWRV error overlay with no recovery path.
            vi.mocked(_http.get).mockRejectedValue(new Error("Network Error"));

            const result = await getTrusts();

            expect(result).toEqual([]);
        });
    });
});
