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
import { downloadTrustKit,
    getHubHealth,
    getTrustDecisions,
    getTrustKitStatus,
    getTrusts,
    getTrustStatuses,
    isImageTag,
    rotateTrustKit } from "@/services/trust-service";

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
        vi.mocked(_http.post).mockReset();
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

    describe("getTrustStatuses", () => {
        it("GETs the same authenticated /trust endpoint and returns the array", async () => {
            // Regression for #557: the Connection Status page must source its
            // data from an authenticated (non-admin) endpoint so Researcher /
            // Viewer roles don't get a 403 and a perpetual spinner. Post-#609
            // that endpoint is the consolidated GET /trust.
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

            expect(_http.get).toHaveBeenCalledWith("/trust");
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

    describe("getTrustDecisions", () => {
        it("GETs one trust's decisions and returns the array (FLIP#1258)", async () => {
            const decisions = [{
                projectId: "p1",
                status: "PENDING"
            }];
            vi.mocked(_http.get).mockResolvedValue({ data: decisions } as never);

            const result = await getTrustDecisions("t-1");

            expect(_http.get).toHaveBeenCalledWith("/trust/t-1/decisions");
            expect(result).toEqual(decisions);
        });

        it("returns [] when the backend body is not an array", async () => {
            // My Trust filters this list into its two sections; a non-array body would throw there.
            vi.mocked(_http.get).mockResolvedValue({ data: { error: "oops" } } as never);

            expect(await getTrustDecisions("t-1")).toEqual([]);
        });
    });

    describe("getHubHealth", () => {
        it("GETs the hub's /health and returns its status and version", async () => {
            const health = {
                status: "ok",
                version: "v0.15.0"
            };
            vi.mocked(_http.get).mockResolvedValue({ data: health } as never);

            const result = await getHubHealth();

            expect(_http.get).toHaveBeenCalledWith("/health");
            expect(result).toEqual(health);
        });
    });

    describe("isImageTag", () => {
        it.each(["v0.15.0", "v0.15.0-rc.1", "sha-1a2b3c4"])("accepts the CI image tag %s", (tag) => {
            expect(isImageTag(tag)).toBe(true);
        });

        it.each(["0.6.0", "sha-xyz", "", null, undefined])("rejects %s, which is not a CI image tag", (tag) => {
            expect(isImageTag(tag)).toBe(false);
        });
    });

    describe("trust kit", () => {
        const kit = {
            file_name: "flip-trust-kit-GSTT.tar.gz.enc",
            kind: "full",
            passphrase: "one-time",  // pragma: allowlist secret
            content_base64: "a2l0"
        };

        it("GETs one trust's kit status", async () => {
            const status = {
                keys_issued: false,
                download_count: 0
            };
            vi.mocked(_http.get).mockResolvedValue({ data: status } as never);

            expect(await getTrustKitStatus("t-1")).toEqual(status);
            expect(_http.get).toHaveBeenCalledWith("/trust/t-1/kit");
        });

        it("POSTs to download the kit and returns the file with its passphrase", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: kit } as never);

            expect(await downloadTrustKit("t-1")).toEqual(kit);
            expect(_http.post).toHaveBeenCalledWith("/trust/t-1/kit");
        });

        it("POSTs the typed trust code to issue new keys", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: kit } as never);

            expect(await rotateTrustKit("t-1", "GSTT")).toEqual(kit);
            expect(_http.post).toHaveBeenCalledWith("/trust/t-1/kit/rotate", { confirm_code: "GSTT" });
        });

        it("lets a refusal reach the caller, which shows the hub's reason", async () => {
            vi.mocked(_http.post).mockRejectedValue(new Error("409"));

            await expect(downloadTrustKit("t-1")).rejects.toThrow("409");
        });
    });
});
