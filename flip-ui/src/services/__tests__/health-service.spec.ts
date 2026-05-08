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
import { getHealth, IHealthResponse } from "@/services/health-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("health-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
    });

    describe("getHealth", () => {
        it("GETs the supplied URL and returns the response payload unchanged", async () => {
            const payload: IHealthResponse[] = [
                {
                    trustId: "t-1",
                    trustName: "Trust_1",
                    online: true
                },
                {
                    trustId: "t-2",
                    trustName: "Trust_2",
                    online: false
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: payload } as never);

            const result = await getHealth("/health");

            expect(_http.get).toHaveBeenCalledWith("/health");
            expect(result).toEqual(payload);
        });

        it("returns an empty array when the backend reports no trusts", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: [] } as never);

            const result = await getHealth("/health");

            expect(result).toEqual([]);
        });

        it("propagates underlying errors so the polling caller can react", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("502 Bad Gateway"));

            await expect(getHealth("/health")).rejects.toThrow("502 Bad Gateway");
        });
    });
});
