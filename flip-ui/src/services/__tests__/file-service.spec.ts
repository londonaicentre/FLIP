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

const httpGet = vi.fn();
const httpDelete = vi.fn();
const httpPost = vi.fn();
const fetchJobTypesMock = vi.fn();

vi.mock("@/services/api", () => ({
    _http: {
        get: (...args: unknown[]) => httpGet(...args),
        delete: (...args: unknown[]) => httpDelete(...args),
        post: (...args: unknown[]) => httpPost(...args)
    }
}));

vi.mock("@/services/model-service", async () => {
    const actual = await vi.importActual<typeof import("@/services/model-service")>(
        "@/services/model-service"
    );

    return {
        ...actual,
        fetchJobTypes: (...args: unknown[]) => fetchJobTypesMock(...args)
    };
});

import { FileUploadStatus } from "@/interfaces/model/types";
import { deleteModelFile,
    downloadModelFile,
    getJobTypeFromConfig,
    getModelConfig,
    processScannedFile,
    resolveModelConfigState } from "@/services/file-service";
import { DEFAULT_JOB_TYPE } from "@/services/model-service";

const jobTypes = {
    standard: ["trainer.py", "config.json"],
    diffusion: ["trainer.py", "config.json", "diffusion.py"]
};

const configBlob = (payload: unknown): { data: Blob } => {
    const data = new Blob([JSON.stringify(payload)], { type: "application/json" });

    return { data };
};

describe("getJobTypeFromConfig", () => {
    beforeEach(() => {
        httpGet.mockReset();
        fetchJobTypesMock.mockReset();
    });

    it("returns the job_type from config.json when it is valid", async () => {
        httpGet.mockResolvedValueOnce(configBlob({ job_type: "diffusion" }));
        const result = await getJobTypeFromConfig("model-1", jobTypes);
        expect(result).toBe("diffusion");
        expect(fetchJobTypesMock).not.toHaveBeenCalled();
    });

    it("falls back to DEFAULT_JOB_TYPE when job_type is not in the job types map", async () => {
        httpGet.mockResolvedValueOnce(configBlob({ job_type: "unknown" }));
        const result = await getJobTypeFromConfig("model-2", jobTypes);
        expect(result).toBe(DEFAULT_JOB_TYPE);
    });

    it("falls back to DEFAULT_JOB_TYPE when config.json has no job_type field", async () => {
        httpGet.mockResolvedValueOnce(configBlob({ something_else: true }));
        const result = await getJobTypeFromConfig("model-3", jobTypes);
        expect(result).toBe(DEFAULT_JOB_TYPE);
    });

    it("falls back to DEFAULT_JOB_TYPE when config.json cannot be parsed", async () => {
        httpGet.mockResolvedValueOnce({ data: new Blob(["not-json"], { type: "application/json" }) });
        const result = await getJobTypeFromConfig("model-4", jobTypes);
        expect(result).toBe(DEFAULT_JOB_TYPE);
    });

    it("falls back to DEFAULT_JOB_TYPE when config.json is not yet uploaded (404)", async () => {
        httpGet.mockRejectedValueOnce(Object.assign(new Error("not found"), {
            isAxiosError: true,
            response: { status: 404 }
        }));
        const result = await getJobTypeFromConfig("model-5", jobTypes);
        expect(result).toBe(DEFAULT_JOB_TYPE);
    });

    it("propagates non-404 fetch errors so the caller can decide retry policy", async () => {
        httpGet.mockRejectedValueOnce(Object.assign(new Error("bad gateway"), {
            isAxiosError: true,
            response: { status: 502 }
        }));
        await expect(getJobTypeFromConfig("model-5b", jobTypes)).rejects.toThrow("bad gateway");
    });

    it("fetches job types from the API when none are provided", async () => {
        fetchJobTypesMock.mockResolvedValueOnce(jobTypes);
        httpGet.mockResolvedValueOnce(configBlob({ job_type: "diffusion" }));
        const result = await getJobTypeFromConfig("model-6");
        expect(fetchJobTypesMock).toHaveBeenCalledTimes(1);
        expect(result).toBe("diffusion");
    });
});

describe("resolveModelConfigState", () => {
    beforeEach(() => {
        httpGet.mockReset();
        fetchJobTypesMock.mockReset();
    });

    it("reports no change when config.json status matches previous status", async () => {
        const files = [{
            name: "config.json",
            status: FileUploadStatus.COMPLETED
        }];
        const result = await resolveModelConfigState(
            files,
            FileUploadStatus.COMPLETED,
            jobTypes,
            "model-id"
        );
        expect(result.changed).toBe(false);
        expect(httpGet).not.toHaveBeenCalled();
    });

    it("reports no change when config.json is absent and previous status was null", async () => {
        const files = [{
            name: "trainer.py",
            status: FileUploadStatus.COMPLETED
        }];
        const result = await resolveModelConfigState(files, null, jobTypes, "model-id");
        expect(result.changed).toBe(false);
        expect(httpGet).not.toHaveBeenCalled();
    });

    it("returns changed:false on transient fetch failure so the next poll can retry", async () => {
        httpGet.mockRejectedValueOnce(Object.assign(new Error("bad gateway"), {
            isAxiosError: true,
            response: { status: 502 }
        }));
        const files = [{
            name: "config.json",
            status: FileUploadStatus.COMPLETED
        }];
        const result = await resolveModelConfigState(
            files,
            FileUploadStatus.SCANNING,
            jobTypes,
            "model-id"
        );
        expect(result.changed).toBe(false);
        expect(httpGet).toHaveBeenCalledTimes(1);
    });

    it("transitions to SCANNING without downloading config.json", async () => {
        const files = [{
            name: "config.json",
            status: FileUploadStatus.SCANNING
        }];
        const result = await resolveModelConfigState(files, null, jobTypes, "model-id");
        if (!result.changed) throw new Error("expected changed:true");
        expect(result.configStatus).toBe(FileUploadStatus.SCANNING);
        expect(result.jobType).toBe(DEFAULT_JOB_TYPE);
        expect(result.requiredFiles).toEqual(jobTypes.standard);
        expect(httpGet).not.toHaveBeenCalled();
    });

    it("downloads config.json and resolves the job type once status becomes COMPLETED", async () => {
        httpGet.mockResolvedValueOnce(configBlob({ job_type: "diffusion" }));
        const files = [{
            name: "config.json",
            status: FileUploadStatus.COMPLETED
        }];
        const result = await resolveModelConfigState(
            files,
            FileUploadStatus.SCANNING,
            jobTypes,
            "model-id"
        );
        if (!result.changed) throw new Error("expected changed:true");
        expect(result.configStatus).toBe(FileUploadStatus.COMPLETED);
        expect(result.jobType).toBe("diffusion");
        expect(result.requiredFiles).toEqual(jobTypes.diffusion);
        expect(httpGet).toHaveBeenCalledTimes(1);
    });

    it("resets to defaults when config.json is removed", async () => {
        const files = [{
            name: "trainer.py",
            status: FileUploadStatus.COMPLETED
        }];
        const result = await resolveModelConfigState(
            files,
            FileUploadStatus.COMPLETED,
            jobTypes,
            "model-id"
        );
        if (!result.changed) throw new Error("expected changed:true");
        expect(result.configStatus).toBeNull();
        expect(result.jobType).toBe(DEFAULT_JOB_TYPE);
        expect(result.requiredFiles).toEqual(jobTypes.standard);
        expect(httpGet).not.toHaveBeenCalled();
    });
});

describe("downloadModelFile", () => {
    beforeEach(() => {
        httpGet.mockReset();
    });

    it("GETs the URL with responseType=blob and returns the body", async () => {
        // The blob responseType is the load-bearing detail: without it
        // axios would JSON-parse a presigned-url body and throw on
        // anything non-JSON (e.g. trainer.py). Test pins it.
        const blob = new Blob(["payload"]);
        httpGet.mockResolvedValueOnce({ data: blob });

        const result = await downloadModelFile("/files/model/m-1/trainer.py");

        expect(httpGet).toHaveBeenCalledWith(
            "/files/model/m-1/trainer.py",
            { responseType: "blob" }
        );
        expect(result).toBe(blob);
    });
});

describe("getModelConfig", () => {
    beforeEach(() => {
        httpGet.mockReset();
    });

    it("returns the parsed config when the blob is valid JSON", async () => {
        httpGet.mockResolvedValueOnce({ data: new Blob([JSON.stringify({ job_type: "diffusion" })]) });

        const result = await getModelConfig("m-1");

        expect(result).toEqual({ job_type: "diffusion" });
    });

    it("URL-encodes the literal config.json filename", async () => {
        httpGet.mockResolvedValueOnce({ data: new Blob([JSON.stringify({})]) });

        await getModelConfig("m-1");

        expect(httpGet).toHaveBeenCalledWith(
            "/files/model/m-1/config.json",
            { responseType: "blob" }
        );
    });

    it("returns null when config.json is not yet uploaded (404)", async () => {
        httpGet.mockRejectedValueOnce(Object.assign(new Error("not found"), {
            isAxiosError: true,
            response: { status: 404 }
        }));

        const result = await getModelConfig("m-1");

        expect(result).toBeNull();
    });

    it("returns null on unparseable JSON so callers fall back to defaults", async () => {
        // The model could legitimately have a half-uploaded config.json
        // that's not yet valid JSON; treating that as "missing" lets the
        // poller retry on the next tick instead of crashing.
        const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
        httpGet.mockResolvedValueOnce({ data: new Blob(["not-json"]) });

        const result = await getModelConfig("m-1");

        expect(result).toBeNull();
        expect(consoleWarn).toHaveBeenCalled();
        consoleWarn.mockRestore();
    });

    it("re-throws non-404 axios errors so callers can retry", async () => {
        // 5xx / network failures must propagate — a silent null would
        // lock the UI to DEFAULT_JOB_TYPE for the rest of the session.
        httpGet.mockRejectedValueOnce(Object.assign(new Error("bad gateway"), {
            isAxiosError: true,
            response: { status: 502 }
        }));

        await expect(getModelConfig("m-1")).rejects.toThrow("bad gateway");
    });
});

describe("deleteModelFile", () => {
    beforeEach(() => {
        httpDelete.mockReset();
    });

    it("DELETEs the URL and returns the response body", async () => {
        httpDelete.mockResolvedValueOnce({ data: "ok" });

        const result = await deleteModelFile("/files/model/m-1/trainer.py");

        expect(httpDelete).toHaveBeenCalledWith("/files/model/m-1/trainer.py");
        expect(result).toBe("ok");
    });
});

describe("processScannedFile", () => {
    beforeEach(() => {
        httpPost.mockReset();
    });

    it("POSTs to the URL and returns the response body", async () => {
        httpPost.mockResolvedValueOnce({ data: "scanned" });

        const result = await processScannedFile("/files/model/m-1/trainer.py/process");

        expect(httpPost).toHaveBeenCalledWith("/files/model/m-1/trainer.py/process");
        expect(result).toBe("scanned");
    });
});
