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
import { getSiteDetails, updateSiteDetails } from "@/services/site-service";
import type { ISiteDetails } from "@/store/siteDetailsStore";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("site-service", () => {
    const fixture: ISiteDetails = {
        banner: {
            message: "Heads up",
            link: "https://example.test",
            enabled: true
        },
        deploymentMode: false,
        maxReimportCount: 3
    };

    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
        vi.mocked(_http.put).mockReset();
    });

    describe("getSiteDetails", () => {
        it("GETs the URL and returns the parsed body", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: fixture } as never);

            const result = await getSiteDetails("/site/details");

            expect(_http.get).toHaveBeenCalledWith("/site/details");
            expect(result).toEqual(fixture);
        });

        it("propagates errors so the store can fall back to defaults", async () => {
            vi.mocked(_http.get).mockRejectedValue(new Error("500"));

            await expect(getSiteDetails("/site/details")).rejects.toThrow("500");
        });
    });

    describe("updateSiteDetails", () => {
        it("PUTs the payload and returns the updated body", async () => {
            // The backend echoes the saved value, which the store uses to
            // re-render the banner without an extra fetch — pin the verb
            // and the body to guard against an accidental switch to POST.
            vi.mocked(_http.put).mockResolvedValue({ data: fixture } as never);

            const result = await updateSiteDetails("/site/details", fixture);

            expect(_http.put).toHaveBeenCalledWith("/site/details", fixture);
            expect(result).toEqual(fixture);
        });
    });
});
