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




import { TrustsResults } from "@/interfaces/cohort-query/types";
import { FileInfo, FileTableRow } from "@/interfaces/model/types";
import type { IStep } from "@/interfaces/steps";
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
    trust_ids: string[];
}

export interface IModel {
    id: string;
    name: string;
    description: string;
    ownerId: string;
    projectId: string;
    // Optional only because cached payloads from before the backend exposed
    // it on the project-models list may still be in flight on first paint.
    status?: ModelStatus;
    // Participating trusts for the mobile list's chips (design 5a). Optional:
    // the project-models endpoint doesn't send it yet — the chips line renders
    // only once the backend does.
    trusts?: IModelSummaryTrust[];
}

export interface ILog {
    id: string;
    modelId: string;
    logDate: string;
    success: boolean;
    trustName: string | null;
    // Trust short code (GSTT/KCH) for compact display; the 1-based federated
    // round the row belongs to. Both null on hub rows and legacy entries.
    trustCode?: string | null;
    globalRound?: number | null;
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
    creationTimestamp?: string | null;
    preparedAt?: string | null;
    trainingStartedAt?: string | null;
    resultsUploadedAt?: string | null;
    // The trusts the run was dispatched to. Empty before dispatch.
    trusts?: IModelSummaryTrust[];
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

/**
 * Server-issued presigned POST policy. ``fields`` must be appended to a
 * ``multipart/form-data`` body verbatim, with the file last under the field
 * name ``file``. ``maxBytes`` mirrors the size cap baked into the policy so
 * the UI can fail fast on oversized files.
 */
export interface IPreSignedUploadPolicy {
    url: string;
    fields: Record<string, string>;
    maxBytes: number;
}

export type ModelStatus =
    "PENDING" |
    "INITIATED" |
    "PREPARED" |
    "TRAINING_STARTED" |
    "RESULTS_UPLOADED" |
    "RESULTS_UPLOAD_FAILED" |
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
    // Appended last so the existing ordinal comparisons keep working: training
    // finished but the results upload failed, so it sorts after RESULTS_UPLOADED.
    "RESULTS_UPLOAD_FAILED",
}

const MODEL_STATUS_LABELS: Record<ModelStatus, string> = {
    PENDING: "Model Created",
    INITIATED: "Model Queued",
    PREPARED: "Model Prepared",
    TRAINING_STARTED: "Training Started",
    RESULTS_UPLOADED: "Results Uploaded",
    RESULTS_UPLOAD_FAILED: "Results Upload Failed",
    ERROR: "Error",
    STOPPED: "Stopped"
};

/** Human-readable label for a model status. Falls back to "—" if unknown. */
export function modelStatusLabel(status: ModelStatus | undefined): string {
    return status ? MODEL_STATUS_LABELS[status] ?? "—" : "—";
}

/** True for terminal failure / cancellation states (drives the red-cross icon). */
export function isModelStatusError(status: ModelStatus | undefined): boolean {
    return status === "ERROR" || status === "STOPPED" || status === "RESULTS_UPLOAD_FAILED";
}

/**
 * Pill classes for the model status chip (the /models-page idiom: coloured
 * pill with a status dot inside). Whole literal Tailwind classes so the JIT
 * compiler emits them.
 */
export function modelStatusPillClass(status: ModelStatus | undefined): string {
    if (isModelStatusError(status)) {
        return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200";
    }
    if (status === "RESULTS_UPLOADED") {
        return "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100";
    }
    if (status === "TRAINING_STARTED") {
        return "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/40 dark:text-fuchsia-200";
    }
    if (status === "PREPARED") {
        return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
    }

    return "bg-gray-200 text-gray-700 dark:bg-dark-raised dark:text-gray-300";
}

/** Dot classes for the model status chip — matches modelStatusPillClass tones. */
export function modelStatusDotClass(status: ModelStatus | undefined): string {
    if (isModelStatusError(status)) return "bg-red-500";
    if (status === "RESULTS_UPLOADED") return "bg-emerald-500";
    if (status === "TRAINING_STARTED") return "bg-fuchsia-500";
    if (status === "PREPARED") return "bg-amber-500";

    return "bg-gray-400";
}

/**
 * Maps a string model status (e.g. "PENDING") to its ModelStatusEnum ordinal.
 * Unknown or undefined statuses fall back to ERROR, so a stale UI bundle that
 * receives a newer status degrades gracefully instead of crashing.
 */
export function getStatusEnumValue(status: string | undefined): number {
    if (status && status in ModelStatusEnum) {
        const value = ModelStatusEnum[status as keyof typeof ModelStatusEnum];
        // Numeric enums carry a reverse mapping, so a numeric-string key like "0"
        // resolves to the member NAME (a string), not an ordinal. Only return real
        // numeric ordinals; anything else degrades to ERROR below.
        if (typeof value === "number") {
            return value;
        }
    }

    return ModelStatusEnum.ERROR;
}

/**
 * Builds the four-step lifecycle tracker (Created → Prepared → Training → Uploaded)
 * shown on the model page, derived from the model's single status value.
 *
 * When training is stopped or errors, prior completed steps stay completed (✅)
 * rather than showing 🚫. RESULTS_UPLOAD_FAILED means training finished but the
 * post-training results upload failed, so "Training" stays completed and only
 * "Results Uploaded" shows the error. See issue #29.
 *
 * Per-step dates (creation/prepared/training/results timestamps) are layered on
 * by the caller, which holds the model record; this helper is purely status-driven.
 */
export function buildModelSteps(status: ModelStatus | undefined): IStep[] {
    const statusValue = getStatusEnumValue(status);
    const isStopped = statusValue === ModelStatusEnum.STOPPED;
    const isError = statusValue === ModelStatusEnum.ERROR;
    const isUploadFailed = statusValue === ModelStatusEnum.RESULTS_UPLOAD_FAILED;
    // RESULTS_UPLOAD_FAILED (ordinal 7) already satisfies the >= PREPARED / > TRAINING_STARTED
    // comparisons in the "completed" flags below, so isUploadFailed is technically redundant
    // there today. It is kept explicit so the steps stay correct if the enum is reordered, and
    // to mirror its load-bearing use in the inProgress / error flags.

    return [
        {
            id: "01",
            name: "Model Created",
            completed: true
        },
        {
            id: "02",
            name: "Model Prepared",
            description: statusValue === ModelStatusEnum.INITIATED ? "Model Queued" : undefined,
            inProgress: statusValue === ModelStatusEnum.INITIATED,
            completed: statusValue >= ModelStatusEnum.PREPARED || isStopped || isError || isUploadFailed
        },
        {
            id: "03",
            name: "Training",
            description:
                (statusValue >= ModelStatusEnum.PREPARED && statusValue < ModelStatusEnum.RESULTS_UPLOADED)
                    ? "In Progress" : undefined,
            inProgress: statusValue >= ModelStatusEnum.PREPARED && !isStopped && !isError && !isUploadFailed,
            completed: statusValue > ModelStatusEnum.TRAINING_STARTED || isUploadFailed,
            error: isError,
            stopped: isStopped
        },
        {
            id: "04",
            name: "Results Uploaded",
            completed: statusValue === ModelStatusEnum.RESULTS_UPLOADED,
            error: isError || isUploadFailed,
            stopped: isStopped
        }
    ];
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
        return { [DEFAULT_JOB_TYPE]: ["trainer.py", "validator.py", "models.py", "config.json"] };
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

/** A trust participating in a model's federated run, for the Models-list chips. */
export interface IModelSummaryTrust {
    id: string;
    name: string;
    code?: string | null;
}

/**
 * One row of the estate-wide Models list (issue #726): a model joined with its
 * owning project, the owner's display name and the model's run trusts. `trusts`
 * is empty until training is initiated (no trusts are assigned before dispatch).
 */
export interface IModelSummary {
    id: string;
    name: string;
    // Optional only until the deployed API ships the field (design 6a): the
    // mobile rows render the description line when it is present.
    description?: string;
    // Required: the /models endpoint returns a status for every row (unlike the
    // per-project IModel list, whose cached payloads predate the status column).
    status: ModelStatus;
    projectId: string;
    projectName: string;
    ownerId: string;
    ownerName?: string | null;
    trusts: IModelSummaryTrust[];
}

/**
 * A page of the estate-wide Models list plus per-status totals for the filter tiles.
 * ``statusCounts`` maps each ``ModelStatus`` to its count across the caller's
 * access-scoped set (honouring search, ignoring the active status filter). Statuses
 * with no models are omitted, so the map is partial.
 */
export interface IModelsPage extends IPaginatedResponse<IModelSummary> {
    statusCounts: Partial<Record<ModelStatus, number>>;
}

/** Fetch the paginated, access-scoped list of models across every project the user can see. */
export async function getAllModels(url: string): Promise<IModelsPage> {
    const response = await _http.get<IModelsPage>(url);

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

export async function uploadModelFile(policy: IPreSignedUploadPolicy, file: File): Promise<void> {
    const form = new FormData();
    for (const [key, value] of Object.entries(policy.fields)) {
        form.append(key, value);
    }
    form.append("file", file);

    const response = await fetch(policy.url, {
        method: "POST",
        body: form
    });

    if (!response.ok) {
        // S3 rejects oversized or policy-violating uploads at the edge —
        // surface that as a thrown error so the caller can mark the file
        // ERROR rather than silently treating an HTML/XML 4xx body as a
        // successful upload.
        throw new Error(`Upload rejected by storage (status ${response.status})`);
    }
}

export async function getPreSignedUrl(
    url: string,
    body: IPreSignedUrlBody
): Promise<IPreSignedUploadPolicy | null> {
    const response = await _http.post<IPreSignedUploadPolicy>(url, body);

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

