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




import { type AxiosResponse, isAxiosError } from "axios";

import { IS_DEMO } from "@/demo/bootstrap";
import { FileUploadStatus } from "@/interfaces/model/types";

import { _http } from "./api";
import { DEFAULT_JOB_TYPE,
    getRequiredFilesForJobType,
    isValidJobType,
    type JobType,
    type JobTypesResponse } from "./model-service";

/**
 * Typed view of `config.json`. Only `job_type` is consumed by the UI; other
 * fields are passed through as `unknown`.
 */
export interface IModelConfig {
    job_type?: JobType;
    [key: string]: unknown;
}

export interface IPresignedDownloadResponse {
    url: string;
    fileName: string;
}

/**
 * Asks flip-api for a short-lived presigned S3 GET URL for a model file.
 *
 * A tiny JSON response, safe under the shared axios client's timeout. What to
 * do with the URL is the caller's choice: navigate the browser straight to it
 * (streamed download, no memory bound — S3 serves it with
 * `Content-Disposition: attachment`) or fetch its bytes into a Blob when the
 * content itself is needed (config.json parsing, zip bundling).
 */
export const getModelFileDownloadUrl = async (url: string): Promise<IPresignedDownloadResponse> => {
    const response: AxiosResponse<IPresignedDownloadResponse> = await _http.get(url);

    return response.data;
};

/**
 * Downloads a model file's bytes.
 *
 * Two steps: first ask flip-api for a short-lived presigned S3 URL, then
 * fetch the actual bytes directly from S3 with the global `fetch` API.
 * `fetch` is used instead of the shared axios client for the byte transfer
 * because that client would prepend `baseURL`, attach an `Authorization`
 * header S3 doesn't expect, and re-apply the very request timeout this
 * two-step flow exists to avoid (large files can take far longer than 30s
 * to transfer). Note the returned Blob lives in memory — for a plain
 * "save this file" action prefer navigating to
 * `getModelFileDownloadUrl(...).url` instead.
 */
export const downloadModelFile = async (url: string): Promise<Blob> => {
    // The public demo has no S3 and no network: the two-step presigned flow
    // cannot work there twice over. The demo API returns the file body itself
    // rather than a `{url, fileName}` envelope, so the presign step yielded
    // `undefined` and `fetch(undefined)` threw on every poll tick — leaving both
    // evaluation models silently falling back to jobType "standard" (FLIP#794
    // review); and even a well-formed URL would be blocked by the demo's
    // `connect-src 'none'` CSP, which is what makes "zero egress" true.
    // Requesting the body through the shared axios client instead keeps it
    // inside Mirage's XHR interception, where the response interceptor in
    // services/api.ts wraps the string body back into a Blob so this function's
    // contract is identical in both builds.
    if (IS_DEMO) {
        const demoResponse = await _http.get<Blob>(url, { responseType: "blob" });

        return demoResponse.data;
    }

    const presigned = await getModelFileDownloadUrl(url);
    const response = await fetch(presigned.url);

    if (!response.ok) {
        // An expired/malformed presigned URL resolves normally under fetch
        // (unlike axios, which throws on a non-2xx) — without this check
        // response.blob() would silently return S3's XML error body as if
        // it were the file's real bytes.
        throw new Error(`Download rejected by storage (status ${response.status})`);
    }

    return response.blob();
};

/**
 * Fetches and parses the `config.json` file for a model.
 *
 * Returns `null` for legitimate "no usable config" cases — a 404 (file not
 * yet uploaded) or unparseable JSON. Transient network/server errors
 * (5xx, CORS, timeout) are re-thrown so the caller can distinguish "config
 * genuinely missing" from "we couldn't reach S3 right now" and retry on the
 * next poll instead of silently locking in a default.
 */
export const getModelConfig = async (modelId: string): Promise<IModelConfig | null> => {
    const path = `/files/model/${modelId}/${encodeURIComponent("config.json")}`;
    let blob: Blob;
    try {
        blob = await downloadModelFile(path);
    } catch (error) {
        if (isAxiosError(error) && error.response?.status === 404) {
            return null;
        }
        throw error;
    }
    try {
        const text = await blob.text();

        return JSON.parse(text) as IModelConfig;
    } catch (error) {
        console.warn(
            `[getModelConfig] config.json for model ${modelId} could not be parsed; treating as missing:`,
            error
        );

        return null;
    }
};

/**
 * Extracts the job_type from `config.json`, validating against available job
 * types from the API. Returns the default for "no usable config" cases (file
 * missing, unparseable, missing/invalid `job_type` field). Transient fetch
 * failures from `getModelConfig` propagate so the caller can decide retry
 * policy.
 *
 * `jobTypes` is passed in rather than fetched here so the page owns the single
 * `fetchJobTypes` call and the single place its failure is surfaced.
 */
export const getJobTypeFromConfig = async (
    modelId: string,
    jobTypes: JobTypesResponse
): Promise<JobType> => {
    const config = await getModelConfig(modelId);

    if (config && config.job_type && isValidJobType(jobTypes, config.job_type)) {
        return config.job_type;
    }

    // Default to standard if:
    // - config.json hasn't been uploaded (404)
    // - config.json doesn't have job_type field
    // - job_type has an invalid/unknown value
    return DEFAULT_JOB_TYPE;
};

/**
 * Result of resolving the model's `config.json` state for a poll tick.
 *
 * `{ changed: false }` — caller does nothing this tick. Encompasses both the
 * steady state (status hasn't moved) and a transient fetch failure (the next
 * poll will retry because `previousStatus` was not advanced).
 *
 * `{ changed: true, ... }` — caller updates its tracker and downstream refs
 * (`currentJobType`, `requiredFiles`) from the returned values.
 *
 * Only the `changed: true` arm exposes `configStatus`/`jobType`/`requiredFiles`,
 * so a caller cannot accidentally read filler values from a no-op result.
 */
export type IResolvedConfigState =
    | { changed: false }
    | {
        changed: true;
        configStatus: FileUploadStatus | null;
        jobType: JobType;
        requiredFiles: string[];
    };

/**
 * Decides whether `config.json` needs re-reading for a poll tick and — if so —
 * resolves the job type and the required files that follow from it.
 *
 * The model detail page polls on a fixed interval. `config.json` is immutable
 * once uploaded, so we only re-download it when its upload status transitions
 * (e.g. `null` → `SCANNING` → `COMPLETED`, or reset on delete).
 *
 * On a transient fetch failure during the `→ COMPLETED` transition, returns
 * `{ changed: false }` so the caller leaves `previousStatus` intact and the
 * next poll re-attempts the fetch — preventing "stuck on DEFAULT_JOB_TYPE"
 * for the rest of the session.
 *
 * @param files - Files currently reported by the model API
 * @param previousStatus - Status observed on the previous tick (`null` if none)
 * @param jobTypes - Cached job type → required files map
 * @param modelId - Model ID, used to fetch `config.json` when status becomes COMPLETED
 */
export const resolveModelConfigState = async (
    files: { name: string; status: FileUploadStatus }[],
    previousStatus: FileUploadStatus | null,
    jobTypes: JobTypesResponse,
    modelId: string
): Promise<IResolvedConfigState> => {
    const configFile = files.find(f => f.name === "config.json");
    const configStatus: FileUploadStatus | null = configFile?.status ?? null;

    if (configStatus === previousStatus) {
        return { changed: false };
    }

    let jobType: JobType = DEFAULT_JOB_TYPE;
    if (configFile && configStatus === FileUploadStatus.COMPLETED) {
        try {
            jobType = await getJobTypeFromConfig(modelId, jobTypes);
        } catch (error) {
            // Transient fetch failure (5xx, network, CORS). Reporting "no
            // change" leaves previousStatus intact so the next poll re-attempts
            // the fetch instead of locking in DEFAULT_JOB_TYPE for the session.
            console.warn(
                `[resolveModelConfigState] Failed to fetch config.json for model ${modelId}; will retry on next poll:`,
                error
            );

            return { changed: false };
        }
    }

    if (process.env.NODE_ENV === "development") {
        console.debug(
            `config.json status changed: ${previousStatus} -> ${configStatus}; jobType=${jobType}`
        );
    }

    return {
        changed: true,
        configStatus,
        jobType,
        requiredFiles: getRequiredFilesForJobType(jobTypes, jobType)
    };
};

export const deleteModelFile = async (url: string): Promise<string> => {
    const response = await _http.delete<string>(url);

    return response.data;
};

export const processScannedFile = async (url: string): Promise<string> => {
    const response = await _http.post<string>(url);

    return response.data;
};
