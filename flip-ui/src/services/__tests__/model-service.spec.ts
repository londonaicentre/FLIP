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
import { buildModelSteps, clearJobTypesCache, createModel, deleteModel, editModel, fetchJobTypes, getAllModels, getDownloadUrlForResults, getLogsForModel, getModel, getModelFileStatus, getModelMetrics, getModels, getPreSignedUrl, getRequiredFilesForJobType, getStatusEnumValue, initialiseTraining, isValidJobType, JobTypesUnavailableError, type ModelStatus, ModelStatusEnum, modelStatusLabelWithQueue, stopTraining, uploadModelFile } from "@/services/model-service";

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

    describe("getAllModels", () => {
        it("GETs the given URL and returns the paged models response with statusCounts", async () => {
            const page = {
                data: [{
                    id: "m1",
                    name: "stroke-v1",
                    projectId: "p1",
                    projectName: "Stroke triage",
                    ownerId: "u1",
                    trusts: []
                }],
                page: 1,
                pageSize: 20,
                totalPages: 1,
                totalRecords: 1,
                statusCounts: { PENDING: 1 }
            };
            vi.mocked(_http.get).mockResolvedValue({ data: page } as never);

            const result = await getAllModels("/models?pageNumber=1&pageSize=20&status=INITIATED,PENDING");

            expect(_http.get).toHaveBeenCalledWith("/models?pageNumber=1&pageSize=20&status=INITIATED,PENDING");
            expect(result).toEqual(page);
        });
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

        it("throws on API failure rather than guessing a required-files list", async () => {
            // The required files differ per FL backend, so any default we invented would be the
            // other backend's list on half of all deployments — and a wrong list makes the
            // training gate permanently unsatisfiable. The page surfaces this instead.
            vi.mocked(_http.get).mockRejectedValue(new Error("503"));
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(fetchJobTypes()).rejects.toThrow(JobTypesUnavailableError);
            expect(consoleError).toHaveBeenCalled();
            consoleError.mockRestore();
        });

        it("throws when the map has no standard job type, rather than unlocking an empty gate", async () => {
            // Every consumer falls back to the standard list, and stringArrayContainsAll(names, [])
            // is true — so accepting this map would mark every model as fully uploaded and enable
            // training against a required-files check with nothing in it.
            vi.mocked(_http.get).mockResolvedValue({ data: { evaluation: ["evaluator.py"] } } as never);
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(fetchJobTypes()).rejects.toThrow(JobTypesUnavailableError);
            consoleError.mockRestore();
        });

        it.each([
            ["an empty body", ""],
            ["a null body", null],
            ["an array", []],
            ["an object with no job types", {}],
            ["a job type whose files are not a list", { standard: "trainer.py" }],
            ["a job type whose files are not strings", { standard: [1, 2] }]
        ])("throws when the response carries %s", async (_label, data) => {
            // A proxy or test harness answering 200 with nothing usable would otherwise be cached
            // as "this deployment has no job types", which is indistinguishable downstream from a
            // deployment that genuinely requires no files.
            vi.mocked(_http.get).mockResolvedValue({ data } as never);
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(fetchJobTypes()).rejects.toThrow(JobTypesUnavailableError);
            consoleError.mockRestore();
        });

        it("does not cache a failure, so the next call retries the API", async () => {
            const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
            vi.mocked(_http.get).mockRejectedValueOnce(new Error("503"));

            await expect(fetchJobTypes()).rejects.toThrow(JobTypesUnavailableError);

            const jobTypes = { standard: ["client_app.py", "config.json", "models.py"] };
            vi.mocked(_http.get).mockResolvedValue({ data: jobTypes } as never);

            await expect(fetchJobTypes()).resolves.toEqual(jobTypes);
            expect(_http.get).toHaveBeenCalledTimes(2);
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

        it("POSTs multipart/form-data with the policy fields then the file last", async () => {
            // S3 presigned POST signs the policy fields, so the form body
            // must carry every server-issued field verbatim and the file
            // must come last under the field name `file` — anything else
            // is rejected by the bucket policy at the edge.
            const fetchMock = vi.fn().mockResolvedValue({ ok: true });
            global.fetch = fetchMock as unknown as typeof fetch;
            const file = new File(["payload"], "trainer.py", { type: "text/x-python" });
            const policy = {
                url: "https://signed.example/upload",
                fields: {
                    key: "models/m-1/trainer.py",
                    "Content-Type": "text/x-python",
                    policy: "base64-policy",
                    "x-amz-signature": "sig"
                },
                maxBytes: 100 * 1024 * 1024
            };

            await uploadModelFile(policy, file);

            expect(fetchMock).toHaveBeenCalledTimes(1);
            const [calledUrl, init] = fetchMock.mock.calls[0] as [string, RequestInit];
            expect(calledUrl).toBe("https://signed.example/upload");
            expect(init.method).toBe("POST");
            expect(init.body).toBeInstanceOf(FormData);
            const form = init.body as FormData;
            expect(Array.from(form.keys())).toEqual([
                "key",
                "Content-Type",
                "policy",
                "x-amz-signature",
                "file"
            ]);
            expect(form.get("key")).toBe("models/m-1/trainer.py");
            expect(form.get("Content-Type")).toBe("text/x-python");
            expect(form.get("file")).toBe(file);
        });

        it("throws when the storage backend rejects the upload", async () => {
            // S3 returns 4xx (with an XML body) for policy violations such
            // as oversized objects. Surfacing this as a thrown error lets
            // the caller mark the file ERROR instead of silently treating
            // a rejection as success.
            global.fetch = vi.fn().mockResolvedValue({
                ok: false,
                status: 403
            }) as unknown as typeof fetch;
            const policy = {
                url: "https://signed.example/upload",
                fields: { key: "models/m-1/trainer.py" },
                maxBytes: 100 * 1024 * 1024
            };

            await expect(
                uploadModelFile(policy, new File(["payload"], "trainer.py"))
            ).rejects.toThrow(/status 403/);
        });
    });

    describe("getPreSignedUrl", () => {
        it("returns the presigned POST policy from the response", async () => {
            const policy = {
                url: "https://signed/upload",
                fields: {
                    key: "models/m-1/trainer.py",
                    "Content-Type": "text/x-python"
                },
                maxBytes: 100 * 1024 * 1024
            };
            vi.mocked(_http.post).mockResolvedValue({ data: policy } as never);

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
            expect(result).toEqual(policy);
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
        it("initialiseTraining POSTs to /fl/initiate/{modelId} with the trust id list", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await initialiseTraining("m-1", { trust_ids: ["t-1", "t-2"] });

            expect(_http.post).toHaveBeenCalledWith(
                "/fl/initiate/m-1",
                { trust_ids: ["t-1", "t-2"] }
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

    describe("buildModelSteps", () => {
        const stepByName = (status: ModelStatus | undefined, name: string) => {
            const step = buildModelSteps(status).find(s => s.name === name);
            if (!step) throw new Error(`step "${name}" not found`);

            return step;
        };

        it("returns the four model lifecycle steps in order", () => {
            expect(buildModelSteps("PENDING").map(s => s.name)).toEqual([
                "Model Created",
                "Model Prepared",
                "Running",
                "Results Uploaded"
            ]);
        });

        it("RESULTS_UPLOADED marks every step completed", () => {
            expect(buildModelSteps("RESULTS_UPLOADED").every(s => s.completed)).toBe(true);
        });

        it("ERROR flags Running as an error", () => {
            // A genuine job failure should still surface on the Running milestone.
            const step = stepByName("ERROR", "Running");
            expect(step.error).toBe(true);
            expect(step.completed).toBeFalsy();
        });

        it("RESULTS_UPLOAD_FAILED keeps Running completed (the job did finish)", () => {
            // The bug this fixes: an upload failure must not paint the Running
            // milestone as failed, because the job itself completed successfully.
            const step = stepByName("RESULTS_UPLOAD_FAILED", "Running");
            expect(step.completed).toBe(true);
            expect(step.error).toBeFalsy();
            expect(step.inProgress).toBeFalsy();
        });

        it("RESULTS_UPLOAD_FAILED flags Results Uploaded as the failed step", () => {
            const step = stepByName("RESULTS_UPLOAD_FAILED", "Results Uploaded");
            expect(step.error).toBe(true);
            expect(step.completed).toBeFalsy();
        });

        it("RESULTS_UPLOAD_FAILED keeps Model Prepared completed", () => {
            expect(stepByName("RESULTS_UPLOAD_FAILED", "Model Prepared").completed).toBe(true);
        });

        it("PREPARED shows Running as Starting — the job is staged but not yet executing", () => {
            const step = stepByName("PREPARED", "Running");
            expect(step.description).toBe("Starting");
            expect(step.inProgress).toBe(true);
        });

        it("RUNNING shows Running as In Progress", () => {
            const step = stepByName("RUNNING", "Running");
            expect(step.description).toBe("In Progress");
            expect(step.inProgress).toBe(true);
        });

        it("RESULTS_UPLOADED clears the Running description", () => {
            expect(stepByName("RESULTS_UPLOADED", "Running").description).toBeUndefined();
        });

        it("an unrecognised status degrades to error handling without throwing", () => {
            // getStatusEnumValue maps anything unknown to ERROR so a stale UI bundle
            // receiving a newer status degrades gracefully rather than crashing.
            expect(() => buildModelSteps("NONSENSE" as ModelStatus)).not.toThrow();
            expect(stepByName("NONSENSE" as ModelStatus, "Running").error).toBe(true);
        });

        it("INITIATED with a queue position describes step 02 as Model Queued (n)", () => {
            const step = buildModelSteps("INITIATED", 2).find(s => s.name === "Model Prepared");
            expect(step?.description).toBe("Model Queued (2)");
        });

        it("INITIATED without a queue position keeps the plain Model Queued description", () => {
            expect(stepByName("INITIATED", "Model Prepared").description).toBe("Model Queued");
        });
    });

    describe("modelStatusLabelWithQueue", () => {
        it("appends the queue position when present", () => {
            expect(modelStatusLabelWithQueue("INITIATED", 2)).toBe("Model Queued (2)");
        });

        it.each([[undefined], [null], [0], [-1]])("omits the suffix for %s", (position) => {
            expect(modelStatusLabelWithQueue("INITIATED", position as number | null | undefined)).toBe("Model Queued");
        });

        it("appends to whatever label the status maps to", () => {
            expect(modelStatusLabelWithQueue("PENDING", 3)).toBe("Model Created (3)");
        });
    });

    describe("getStatusEnumValue", () => {
        it("maps a known status name to its ordinal", () => {
            expect(getStatusEnumValue("RESULTS_UPLOAD_FAILED")).toBe(ModelStatusEnum.RESULTS_UPLOAD_FAILED);
        });

        it("falls back to ERROR for undefined or unrecognised status", () => {
            expect(getStatusEnumValue(undefined)).toBe(ModelStatusEnum.ERROR);
            expect(getStatusEnumValue("NONSENSE")).toBe(ModelStatusEnum.ERROR);
        });

        it("falls back to ERROR for a numeric-string key (numeric-enum reverse mapping)", () => {
            // ModelStatusEnum["0"] reverse-maps to the member NAME ("PENDING"), a string —
            // not an ordinal. The guard must reject it and still return a number.
            const result = getStatusEnumValue("0");
            expect(typeof result).toBe("number");
            expect(result).toBe(ModelStatusEnum.ERROR);
        });
    });
});
