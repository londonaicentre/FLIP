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




import { TrustsResults } from "@/interfaces/cohort-query/types";
import { FileInfo, FileTableRow } from "@/interfaces/model/types";
import { _http, IPaginatedResponse } from "@/services/api";

export interface IModelMetricData {
    yLabel: string;
    xLabel: string;
    metrics: {
        data: {
            xValue: number;
            yValue: number;
        }[],
        seriesLabel: string;
    }[]
}

export interface IInitTraining {
    trusts: string[];
}

export interface IModel {
    id: string;
    name: string;
    description: string;
    ownerId: string;
    projectId: string;
}

export interface ILog {
    id: string;
    modelId: string;
    logDate: string;
    success: boolean;
    trustName: string | null;
    log: string;
}

export interface IModelDashboardQuery {
    name: string;
    query: string;
    results: TrustsResults[];
}

export interface IModelDashboard {
    modelId: string;
    projectId: string;
    modelName: string;
    modelDescription: string;
    status: ModelStatus;
    query: IModelDashboardQuery,
    files: FileInfo[];
}

export interface IModelCreate {
    name: string;
    description: string;
    projectId: string;
}

export interface IModelUpdate {
    name: string;
    description: string;
}

export interface ICreateModelResponse {
    id: string;
}

export interface IUploadedFileResponse {
    body: string;
}

export interface IPreSignedUrlBody {
    fileName: string;
    contentType: string | null;
}

export type ModelStatus =
    "PENDING" |
    "INITIATED" |
    "PREPARED" |
    "TRAINING_STARTED" |
    "RESULTS_UPLOADED" |
    "ERROR" |
    "STOPPED"

export enum ModelStatusEnum {
    "ERROR",
    "STOPPED",
    "PENDING",
    "INITIATED",
    "PREPARED",
    "TRAINING_STARTED",
    "RESULTS_UPLOADED",
}

/**
 * Type alias for job type string.
 * Job types are dynamically loaded from the backend API.
 */
export type JobType = string;

/**
 * Default job type used when no job_type is specified in config.json.
 */
export const DEFAULT_JOB_TYPE: JobType = "standard";

/**
 * Interface for the job types response from the backend.
 * Maps job type names to their required files.
 */
export type JobTypesResponse = Record<string, string[]>;

// Cache for job types to avoid repeated API calls
let _jobTypesCache: JobTypesResponse | null = null;

/**
 * Fetches all job types and their required files from the backend API.
 * Results are cached to avoid repeated API calls.
 * @returns Promise resolving to a record mapping job types to required files
 */
export async function fetchJobTypes(): Promise<JobTypesResponse> {
    if (_jobTypesCache) {
        return _jobTypesCache;
    }

    try {
        const response = await _http.get<JobTypesResponse>("/model/job-types");
        _jobTypesCache = response.data as JobTypesResponse;
        return _jobTypesCache!;
    } catch (error) {
        console.error("[fetchJobTypes] Error fetching job types:", error);
        // Return a minimal default if API fails
        return {
            [DEFAULT_JOB_TYPE]: ["trainer.py", "validator.py", "models.py", "config.json"]
        };
    }
}

/**
 * Clears the job types cache, forcing a fresh fetch on next call.
 */
export function clearJobTypesCache(): void {
    _jobTypesCache = null;
}

/**
 * Returns the required files for a given job type.
 * @param jobTypes - The job types record (from fetchJobTypes)
 * @param jobType - The job type name (defaults to 'standard')
 * @returns Array of required file names
 */
export function getRequiredFilesForJobType(
    jobTypes: JobTypesResponse,
    jobType: JobType = DEFAULT_JOB_TYPE
): string[] {
    return jobTypes[jobType] ?? jobTypes[DEFAULT_JOB_TYPE] ?? [];
}

/**
 * Checks if a job type is valid based on the fetched job types.
 * @param jobTypes - The job types record (from fetchJobTypes)
 * @param jobType - The job type to validate
 * @returns true if the job type exists in the record
 */
export function isValidJobType(jobTypes: JobTypesResponse, jobType: string): boolean {
    return jobType in jobTypes;
}


export async function getModels(url: string): Promise<IPaginatedResponse<IModel>> {
    const response = await _http.get<IPaginatedResponse<IModel>>(url);

    return response.data;
}

export async function getModel(url: string): Promise<IModelDashboard> {
    const response = await _http.post<IModelDashboard>(url);

    return response.data;
}

export async function getModelFileStatus(url: string): Promise<FileTableRow[]> {
    const response = await _http.get<FileTableRow[]>(url);

    return response.data;
}

export async function createModel(url: string, model: IModelCreate): Promise<ICreateModelResponse> {
    const response = await _http.post<ICreateModelResponse>(url, model);

    return response.data;
}

export async function editModel(url: string, model: IModelUpdate): Promise<IModelDashboard> {
    const response = await _http.put<IModelDashboard>(url, model);

    return response.data;
}

export async function deleteModel(url: string): Promise<void> {
    await _http.delete<never>(url);
}

export async function uploadModelFile(url: string, file: Blob): Promise<void> {
    await fetch(url, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": file.type }
    });
}

export async function getPreSignedUrl(url: string, body: IPreSignedUrlBody): Promise<string | null> {

    const response = await _http.post<string>(url, body);

    return response.data ?? null;
}

export async function initialiseTraining(
    modelId: string,
    initTrainingRequestData: IInitTraining
): Promise<void> {
    await _http.post(`/fl/initiate/${modelId}`, initTrainingRequestData);
}

export async function getDownloadUrlForResults(modelId: string): Promise<string[]> {
    const response = await _http.get<string[]>(`/files/model/${modelId}/fl/results`);

    return response.data ?? [];
}

export async function getLogsForModel(url: string): Promise<ILog[]> {
    const response = await _http.get<ILog[]>(url);

    return response.data ?? [];
}

export async function stopTraining(modelId: string): Promise<undefined> {
    await _http.post(`/fl/stop/${modelId}`);

    return;
}

export async function getModelMetrics(url: string): Promise<IModelMetricData[]> {

    const response = await _http.get<IModelMetricData[]>(url);

    return response.data ?? [];
}
