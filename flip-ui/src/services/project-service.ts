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
    // Frozen list of trust IDs that returned a result for this query. Used by
    // ProjectStaging to hide trusts that joined after the query was run — we
    // have no cohort count for them, so the user shouldn't be able to stage
    // against them. Callers that want a "how many trusts ran this?" count
    // take `.length`.
    queriedTrustIds: string[];
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
    // very old seeded users may have no profile row; UI falls back to
    // the email-derived username then.
    ownerName?: string | null;
    ownerEmail: string;
    // Total users with project access — includes the owner (auto-added
    // to ProjectUserAccess on creation), so the UI doesn't need to +1.
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
