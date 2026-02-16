/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




import type { AxiosResponse } from "axios";
import { _http } from "./api";
import { DEFAULT_JOB_TYPE, fetchJobTypes, isValidJobType, type JobType, type JobTypesResponse } from "./model-service";

/**
 * Interface for the config.json file structure.
 * Only includes fields relevant for determining required files.
 */
export interface IModelConfig {
    job_type?: string;
    [key: string]: unknown;
}

export const downloadModelFile = async (url: string): Promise<Blob> => {
  const response: AxiosResponse<Blob> = await _http.get(url, { responseType: "blob" });

  return response.data;
};

/**
 * Fetches and parses the config.json file for a model.
 * @param modelId - The model ID
 * @returns The parsed config object, or null if not found/invalid
 */
export const getModelConfig = async (modelId: string): Promise<IModelConfig | null> => {
    try {
        const path = `/files/model/${modelId}/${encodeURIComponent("config.json")}`;
        const blob = await downloadModelFile(path);
        const text = await blob.text();
        return JSON.parse(text) as IModelConfig;
    } catch {
        // Config file doesn't exist or is invalid JSON
        return null;
    }
};

/**
 * Extracts the job_type from config.json, validating against available job types from the API.
 * Defaults to 'standard' if not found, missing, or invalid.
 * @param modelId - The model ID
 * @param jobTypes - Optional pre-fetched job types. If not provided, will fetch from API.
 * @returns The job type from config.json or 'standard' as default
 */
export const getJobTypeFromConfig = async (
    modelId: string,
    jobTypes?: JobTypesResponse
): Promise<JobType> => {
    try {
        // Fetch job types if not provided
        const availableJobTypes = jobTypes ?? await fetchJobTypes();
        const config = await getModelConfig(modelId);

        console.log("[getJobTypeFromConfig] Fetched config:", config);
        console.log("[getJobTypeFromConfig] job_type value:", config?.job_type);
        console.log("[getJobTypeFromConfig] Available job types:", Object.keys(availableJobTypes));

        // If config exists and has a valid job_type, use it
        if (config && config.job_type && isValidJobType(availableJobTypes, config.job_type)) {
            console.log("[getJobTypeFromConfig] Using job_type from config:", config.job_type);
            return config.job_type;
        }

        // Default to standard if:
        // - config.json couldn't be fetched
        // - config.json doesn't have job_type field
        // - job_type has an invalid/unknown value
        console.log("[getJobTypeFromConfig] Defaulting to standard");
        return DEFAULT_JOB_TYPE;
    } catch (error) {
        // If anything goes wrong, default to standard
        console.error("[getJobTypeFromConfig] Error:", error);
        return DEFAULT_JOB_TYPE;
    }
};

export const deleteModelFile = async (url: string): Promise<string> => {
    const response = await _http.delete<string>(url);

    return response.data;
};

export const processScannedFile = async (url: string): Promise<string> => {
    const response = await _http.post<string>(url);

    return response.data;
};
