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




import { _http, IPaginatedResponse } from "@/services/api";
import type { IProjectUser } from "@/services/user-service";

export type { IProjectUser };

export interface IProjectTrust {
    name: string;
    id: string;
    code?: string | null;
    approved: boolean;
    approvedAt?: string | null;
}

export interface IProjectQuery {
    id: string;
    name: string;
    query: string;
    // Trust IDs the query was dispatched to at submit time. PerTrustResponse
    // uses this as the visibility set so trusts that errored or never
    // responded stay on screen (sent / running / red chip). Callers that
    // want a "how many trusts ran this?" count take `.length`.
    queriedTrustIds: string[];
    // Subset of `queriedTrustIds` whose TrustTask is still PENDING — trust
    // hasn't polled yet. UI shows a "queued" chip instead of "running".
    pendingTrustIds: string[];
    // Subset of `queriedTrustIds` whose TrustTask was cancelled (project
    // approved without them). UI shows a "skipped" chip.
    cancelledTrustIds: string[];
    // Subset of `queriedTrustIds` that posted any QueryResult row (success
    // or error). Stageable = `respondedTrustIds − erroredTrustIds − emptyTrustIds`.
    respondedTrustIds: string[];
    // Subset of `respondedTrustIds` whose response carried an error.
    // Staging additionally excludes these — we have no usable cohort count.
    erroredTrustIds: string[];
    // Subset of `respondedTrustIds` that returned 0 records — genuine zero
    // match or privacy-suppressed below-threshold count (#519). Staging
    // excludes these: there's no cohort to build an imaging project against.
    emptyTrustIds: string[];
    totalCohort: number;
    created?: string | null;
    createdBy?: string | null;
}

export type ProjectStatus = "UNSTAGED" | "STAGED" | "APPROVED";

export type IProject = {
    id: string;
    name: string;
    description: string;
    ownerId: string;
    // Display name of the owner (from UserProfile). Optional because
    // the detail endpoint doesn't surface it (only the list endpoint
    // does); UI falls back to the email-derived username.
    ownerName?: string | null;
    ownerEmail: string;
    // Total users with project access — includes the owner (auto-added
    // to ProjectUserAccess on creation), so the UI doesn't need to +1.
    // Optional for the same reason as ownerName.
    userCount?: number;
    creationtimestamp: string;
    stagedAt?: string | null;
    query?: IProjectQuery;
    approvedTrusts?: IProjectTrust[];
    users: IProjectUser[]
    status: ProjectStatus
}

export interface IProjectCreate {
    name: string;
    description: string;
    users?: string[];
    dicom_to_nifti?: boolean;
}

export interface ICreateProjectResponse {
    id: string;
}

export interface IImagingImportStatus {
    successful: number;
    failed: number;
    processing: number;
    queued: number;
    queueFailed: number;
}

export interface IImagingProjectStatus {
    trustId: string,
    trustName: string,
    projectCreationCompleted: boolean,
    importStatus?: IImagingImportStatus,
    reimportCount?: number,
}

export async function getProject(url: string): Promise<IProject> {
    const response = await _http.get<IProject>(url);

    return response.data;
}

export async function editProject(url: string, project: { name: string; description: string }): Promise<IProject> {
    const response = await _http.put<IProject>(url, project);

    return response.data;
}

export async function getProjects(url: string): Promise<IPaginatedResponse<IProject>> {
    const response = await _http.get<IPaginatedResponse<IProject>>(url);

    return response.data;
}

export async function createProject(url: string, project: IProjectCreate): Promise<ICreateProjectResponse> {
    const response = await _http.post<ICreateProjectResponse>(url, project);

    return response.data;
}

export async function stageProject(url: string, trusts: string[]): Promise<void> {
    await _http.post<never>(url, { trusts: trusts });
}

export async function unstageProject(url: string): Promise<void> {
    await _http.post<never>(url);
}

export async function approveProject(url: string, trusts: string[]): Promise<void> {
    await _http.post<never>(url, { trusts: trusts });
}

export async function deleteProject(url: string): Promise<void> {
    await _http.delete<never>(url);
}

export async function getImagingProjectsStatus(url: string): Promise<IImagingProjectStatus[]> {
    const response = await _http.get<IImagingProjectStatus[]>(url);

    return response.data;
}
