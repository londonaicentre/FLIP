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

import { describe, expect, it, vi } from "vitest";

import { createAdminTrust } from "@/services/admin-trusts-service";
import { _http } from "@/services/api";

vi.mock("@/services/api", () => ({ _http: { post: vi.fn() } }));

describe("createAdminTrust", () => {
    it("POSTs the new trust to /admin/trusts and returns the created trust with its one-time keys", async () => {
        const payload = {
            name: "Guy's Trust",
            code: "GSTT"
        };
        const created = {
            id: "t-1",
            ...payload,
            trust_api_key: "api-key", // pragma: allowlist secret
            fl_kit_slot: "site-1"
        };
        vi.mocked(_http.post).mockResolvedValueOnce({ data: created });

        const result = await createAdminTrust(payload);

        expect(_http.post).toHaveBeenCalledWith("/admin/trusts", payload);
        expect(result).toEqual(created);
    });

    it("lets a refusal reach the caller", async () => {
        vi.mocked(_http.post).mockRejectedValueOnce(new Error("409"));

        await expect(createAdminTrust({
            name: "Dup",
            code: "DUP"
        })).rejects.toThrow("409");
    });
});
