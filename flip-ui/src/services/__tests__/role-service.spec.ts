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
import { getRoles, IRoleResponse } from "@/services/role-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("role-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
    });

    describe("getRoles", () => {
        it("GETs the URL and returns the role list", async () => {
            const payload: IRoleResponse = {
                roles: [
                    {
                        id: "r-1",
                        rolename: "Admin",
                        roledescription: "site admin"
                    },
                    {
                        id: "r-2",
                        rolename: "Researcher",
                        roledescription: "data scientist"
                    }
                ]
            };
            vi.mocked(_http.get).mockResolvedValue({ data: payload } as never);

            const result = await getRoles("/roles");

            expect(_http.get).toHaveBeenCalledWith("/roles");
            expect(result).toEqual(payload);
        });

        it("propagates errors so the page can show a load failure", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("500 Internal Server Error"));

            await expect(getRoles("/roles")).rejects.toThrow("500 Internal Server Error");
        });
    });
});
