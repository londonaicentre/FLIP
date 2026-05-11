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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _http } from "@/services/api";
import { clearJobTypesCache,
    createModel,
    DEFAULT_JOB_TYPE,
    deleteModel,
    editModel,
    fetchJobTypes,
    getDownloadUrlForResults,
    getLogsForModel,
    getModel,
    getModelFileStatus,
    getModelMetrics,
    getModels,
    getPreSignedUrl,
    getRequiredFilesForJobType,
    initialiseTraining,
    isValidJobType,
    stopTraining,
    uploadModelFile } from "@/services/model-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("model-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
        vi.mocked(_http.post).mockReset();
        vi.mocked(_http.put).mockReset();
        vi.mocked(_http.delete).mockReset();
        // Each fetchJobTypes test runs against a fresh cache; without this
        // a single failing test would poison every subsequent test in the
        // suite via the module-level `_jobTypesCache`.
        clearJobTypesCache();
    });

    describe("fetchJobTypes", () => {
        it("GETs /model/job-types and returns the response", async () => {
            const jobTypes = {
                standard: ["trainer.py", "config.json"],
                diffusion: ["trainer.py", "config.json", "diffusion.py"]
            };
            vi.mocked(_http.get).mockResolvedValue({ data: jobTypes } as never);

            const result = await fetchJobTypes();

            expect(_http.get).toHaveBeenCalledWith("/model/job-types");
            expect(result).toEqual(jobTypes);
        });

        it("caches the result so a second call does not hit the API", async () => {
            // The job types map is fetched on the model detail page on
            // every poll tick. Without caching, a 1-second poll would
            // produce 60 unnecessary GETs/minute.
            const jobTypes = { standard: ["trainer.py"] };
            vi.mocked(_http.get).mockResolvedValue({ data: jobTypes } as never);

            await fetchJobTypes();
            await fetchJobTypes();

            expect(_http.get).toHaveBeenCalledTimes(1);
        });

        it("clearJobTypesCache forces a fresh fetch on the next call", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: { standard: [] } } as never);

            await fetchJobTypes();
            clearJobTypesCache();
            await fetchJobTypes();

            expect(_http.get).toHaveBeenCalledTimes(2);
        });

        it("falls back to a minimal default on API failure", async () => {
            // Falling back to a sensible default keeps the upload UI usable
            // while the backend is unreachable, instead of leaving the
            // required-files list empty.
            vi.mocked(_http.get).mockRejectedValue(new Error("503"));
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            const result = await fetchJobTypes();

            expect(result).toEqual({ [DEFAULT_JOB_TYPE]: ["trainer.py", "validator.py", "models.py", "config.json"] });
            expect(consoleError).toHaveBeenCalled();
            consoleError.mockRestore();
        });
    });

    describe("getRequiredFilesForJobType", () => {
        const jobTypes = {
            standard: ["trainer.py", "config.json"],
            diffusion: ["trainer.py", "config.json", "diffusion.py"]
        };

        it("returns the file list for a known job type", () => {
            expect(getRequiredFilesForJobType(jobTypes, "diffusion"))
                .toEqual(["trainer.py", "config.json", "diffusion.py"]);
        });

        it("defaults to the standard job type when no key is provided", () => {
            expect(getRequiredFilesForJobType(jobTypes))
                .toEqual(["trainer.py", "config.json"]);
        });

        it("falls back to the standard list when the requested type is unknown", () => {
            expect(getRequiredFilesForJobType(jobTypes, "no-such-type"))
                .toEqual(["trainer.py", "config.json"]);
        });

        it("returns an empty array when neither the requested type nor 'standard' exists", () => {
            // Final fallback for the (defensive) case where a backend
            // misconfiguration omits the standard key entirely.
            expect(getRequiredFilesForJobType({}, "anything")).toEqual([]);
        });
    });

    describe("isValidJobType", () => {
        const jobTypes = {
            standard: [],
            diffusion: []
        };

        it("returns true for a key that exists in the map", () => {
            expect(isValidJobType(jobTypes, "diffusion")).toBe(true);
        });

        it("returns false for a key that does not exist", () => {
            expect(isValidJobType(jobTypes, "unknown")).toBe(false);
        });
    });

    describe("CRUD wrappers", () => {
        it("getModels GETs the URL and returns the paginated body", async () => {
            const body = {
                page: 1,
                pageSize: 10,
                totalPages: 1,
                totalRecords: 0,
                data: []
            };
            vi.mocked(_http.get).mockResolvedValue({ data: body } as never);

            const result = await getModels("/model?page=1");

            expect(_http.get).toHaveBeenCalledWith("/model?page=1");
            expect(result).toEqual(body);
        });

        it("getModel POSTs to the URL (cohort query is server-side, not idempotent)", async () => {
            // Loading a model dashboard re-runs the cohort query against
            // current trust data, so the backend exposes it as POST even
            // though the UI semantically just "fetches" the model. Test
            // pins the verb to prevent an accidental switch to GET.
            vi.mocked(_http.post).mockResolvedValue({ data: { modelId: "m-1" } } as never);

            await getModel("/model/m-1/dashboard");

            expect(_http.post).toHaveBeenCalledWith("/model/m-1/dashboard");
            expect(_http.get).not.toHaveBeenCalled();
        });

        it("getModelFileStatus GETs the URL", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: [] } as never);

            await getModelFileStatus("/model/m-1/files");

            expect(_http.get).toHaveBeenCalledWith("/model/m-1/files");
        });

        it("createModel POSTs the payload and returns the response", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: { id: "m-1" } } as never);

            const payload = {
                name: "n",
                description: "d",
                projectId: "p-1"
            };
            const result = await createModel("/model", payload);

            expect(_http.post).toHaveBeenCalledWith("/model", payload);
            expect(result).toEqual({ id: "m-1" });
        });

        it("editModel PUTs the payload", async () => {
            vi.mocked(_http.put).mockResolvedValue({ data: {} } as never);

            await editModel("/model/m-1", {
                name: "n",
                description: "d"
            });

            expect(_http.put).toHaveBeenCalledWith("/model/m-1", {
                name: "n",
                description: "d"
            });
        });

        it("deleteModel DELETEs the URL", async () => {
            vi.mocked(_http.delete).mockResolvedValue({ data: undefined } as never);

            await deleteModel("/model/m-1");

            expect(_http.delete).toHaveBeenCalledWith("/model/m-1");
        });
    });

    describe("uploadModelFile", () => {
        const originalFetch = global.fetch;

        afterEach(() => {
            global.fetch = originalFetch;
        });

        it("PUTs the blob to the pre-signed URL with the blob's content type", async () => {
            // Pinned to PUT + the file's own content type because S3's
            // pre-signed URLs sign the method and Content-Type header — a
            // POST or a default `application/octet-stream` would be
            // rejected by the bucket policy.
            const fetchMock = vi.fn().mockResolvedValue({ ok: true });
            global.fetch = fetchMock as unknown as typeof fetch;
            const blob = new Blob(["payload"], { type: "application/json" });

            await uploadModelFile("https://signed.example/upload", blob);

            expect(fetchMock).toHaveBeenCalledWith("https://signed.example/upload", {
                method: "PUT",
                body: blob,
                headers: { "Content-Type": "application/json" }
            });
        });
    });

    describe("getPreSignedUrl", () => {
        it("returns the presigned URL string from the response", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: "https://signed/upload" } as never);

            const result = await getPreSignedUrl(
                "/files/upload",
                {
                    fileName: "trainer.py",
                    contentType: "text/x-python"
                }
            );

            expect(_http.post).toHaveBeenCalledWith(
                "/files/upload",
                {
                    fileName: "trainer.py",
                    contentType: "text/x-python"
                }
            );
            expect(result).toBe("https://signed/upload");
        });

        it("returns null when the backend returns no body", async () => {
            // Defensive: when axios parses an empty 200 response.data is
            // undefined, which the `?? null` collapses to a sentinel the
            // caller switches on to fall back to a retry path.
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            const result = await getPreSignedUrl(
                "/files/upload",
                {
                    fileName: "trainer.py",
                    contentType: null
                }
            );

            expect(result).toBeNull();
        });
    });

    describe("training control", () => {
        it("initialiseTraining POSTs to /fl/initiate/{modelId} with the trust list", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await initialiseTraining("m-1", { trusts: ["t-1", "t-2"] });

            expect(_http.post).toHaveBeenCalledWith(
                "/fl/initiate/m-1",
                { trusts: ["t-1", "t-2"] }
            );
        });

        it("stopTraining POSTs to /fl/stop/{modelId} with no body", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await stopTraining("m-1");

            expect(_http.post).toHaveBeenCalledWith("/fl/stop/m-1");
        });
    });

    describe("results & logs", () => {
        it("getDownloadUrlForResults returns the URLs for a model", async () => {
            const urls = ["https://signed/r1", "https://signed/r2"];
            vi.mocked(_http.get).mockResolvedValue({ data: urls } as never);

            const result = await getDownloadUrlForResults("m-1");

            expect(_http.get).toHaveBeenCalledWith("/files/model/m-1/fl/results");
            expect(result).toEqual(urls);
        });

        it("getDownloadUrlForResults returns [] when the backend body is null", async () => {
            // Some endpoints serialise "no results yet" as an explicit
            // null. The component renders `result.length === 0` as "no
            // results" — leaking the null straight through would crash
            // the table.
            vi.mocked(_http.get).mockResolvedValue({ data: null } as never);

            const result = await getDownloadUrlForResults("m-1");

            expect(result).toEqual([]);
        });

        it("getLogsForModel returns [] when body is null", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: null } as never);

            const result = await getLogsForModel("/logs/m-1");

            expect(result).toEqual([]);
        });

        it("getLogsForModel returns the parsed log list", async () => {
            const logs = [
                {
                    id: "l-1",
                    modelId: "m-1",
                    logDate: "2025-01-01",
                    success: true,
                    trustName: null,
                    log: "ok"
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: logs } as never);

            const result = await getLogsForModel("/logs/m-1");

            expect(result).toEqual(logs);
        });

        it("getModelMetrics returns [] when body is null", async () => {
            vi.mocked(_http.get).mockResolvedValue({ data: null } as never);

            const result = await getModelMetrics("/metrics/m-1");

            expect(result).toEqual([]);
        });

        it("getModelMetrics returns the parsed metrics", async () => {
            const metrics = [{
                yLabel: "loss",
                xLabel: "epoch",
                metrics: [{
                    data: [{
                        xValue: 0,
                        yValue: 1
                    }],
                    seriesLabel: "train"
                }]
            }];
            vi.mocked(_http.get).mockResolvedValue({ data: metrics } as never);

            const result = await getModelMetrics("/metrics/m-1");

            expect(result).toEqual(metrics);
        });
    });
});
