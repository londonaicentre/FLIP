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
import { getFLStatus, getNetDetailedStatus, IFLStatus } from "@/services/fl-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("fl-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
    });

    describe("getFLStatus", () => {
        it("GETs the supplied URL and returns the network list sorted by name", async () => {
            // The dashboard renders networks in the order this function
            // returns them; sort-by-name guarantees deterministic ordering
            // regardless of how the backend serialises the response.
            const networks: IFLStatus[] = [
                {
                    name: "zeta",
                    clients: []
                },
                {
                    name: "alpha",
                    clients: []
                },
                {
                    name: "mike",
                    clients: []
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: networks } as never);

            const result = await getFLStatus("/fl/status");

            expect(_http.get).toHaveBeenCalledWith("/fl/status");
            expect(result.map(n => n.name)).toEqual(["alpha", "mike", "zeta"]);
        });

        it("returns an empty array when the backend returns an empty list", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: [] } as never);

            const result = await getFLStatus("/fl/status");

            expect(result).toEqual([]);
        });

        it("propagates underlying errors so SWRV can surface them", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("503 Service Unavailable"));

            await expect(getFLStatus("/fl/status")).rejects.toThrow("503 Service Unavailable");
        });
    });

    describe("getNetDetailedStatus", () => {
        it("returns the parsed body for a single network", async () => {
            const network: IFLStatus = {
                name: "alpha",
                fl_backend: "flower",
                clients: [
                    {
                        name: "Trust_1",
                        online: true,
                        status: "READY"
                    },
                    {
                        name: "Trust_2",
                        online: false,
                        status: "OFFLINE"
                    }
                ]
            };
            vi.mocked(_http.get).mockResolvedValue({ data: network } as never);

            const result = await getNetDetailedStatus("/fl/network/alpha");

            expect(_http.get).toHaveBeenCalledWith("/fl/network/alpha");
            expect(result).toEqual(network);
        });

        it("propagates underlying errors", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("404 Not Found"));

            await expect(getNetDetailedStatus("/fl/network/missing")).rejects.toThrow("404 Not Found");
        });
    });
});
