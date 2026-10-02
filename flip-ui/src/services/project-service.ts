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

// A trust's decision on a staged project. Every trust starts PENDING when the project is staged.
export type TrustApprovalStatus = "PENDING" | "APPROVED" | "DECLINED";

export interface IProjectTrust {
    name: string;
    id: string;
    code?: string | null;
    status: TrustApprovalStatus;
    // Who made the current decision (user id + display name) and when. All null while PENDING;
    // the decider is also null on approvals recorded before decisions were attributed (FLIP#1318).
    decidedBy?: string | null;
    decidedByName?: string | null;
    decidedAt?: string | null;
    // HUB or SITE: whether the hub admin or the trust's own Trust Admin decided (FLIP#1258).
    decidedAs?: "HUB" | "SITE" | null;
    // True when the trust has a Trust Admin, so only they decide it.
    hasTrustAdmin?: boolean;
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
    // Whether DICOMs are converted to NIfTI on import. Set at creation and
    // immutable thereafter. Only the project-detail endpoint surfaces it, so
    // it's optional (the list endpoint omits it).
    dicom_to_nifti?: boolean;
    // Whether the project has an imaging stage at all. Set at creation and immutable
    // thereafter (FLIP#1071). Off = tabular-only cohort: no XNAT project, no image
    // pull, no imaging status card. Unlike dicom_to_nifti the list endpoint does return
    // it; optional only because a hub predating the flag omits it (absent = imaging).
    has_imaging?: boolean;
}

export interface IProjectCreate {
    name: string;
    description: string;
    users?: string[];
    dicom_to_nifti?: boolean;
    has_imaging?: boolean;
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

// How the hub's newest imaging-status refresh for this trust turned out. The counts in
// `importStatus` are always the last known ones, so anything other than "ok" means they are
// stale and must not be presented as current (FLIP#1022).
export type ImagingConnectionState = "ok" | "unreachable" | "project-missing";

export interface IImagingProjectStatus {
    trustId: string,
    trustName: string,
    projectCreationCompleted: boolean,
    importStatus?: IImagingImportStatus,
    reimportCount?: number,
    connectionState?: ImagingConnectionState,
    // When `importStatus` was last confirmed against the trust (ISO 8601). Absent when no
    // refresh has ever succeeded.
    lastSeenAt?: string | null,
}

// Where one approved trust's approval-time cohort freeze stands, as the hub knows it (FLIP#857).
// "frozen" confirms a frozen membership. "pending" and "failed" cover a trust never frozen —
// training there is refused — and a frozen trust being re-checked, which keeps serving meanwhile.
export type CohortSnapshotState = "frozen" | "pending" | "failed";

// One approved trust's cohort freeze (FLIP#857) — aggregates only. The count fields are the
// approval-time facts, present once the trust has reported a snapshot: the frozen membership
// bounds what the project trains on there (it can shrink, never grow). A rowCount differing
// from approvedRecordCount means the live cohort drifted between submission and approval;
// hasAccessions=false marks a cohort with no imaging to pull. error is a category-only reason:
// why a "failed" trust is not frozen, or, on a "frozen" one, that its last re-check failed.
export interface ICohortSnapshot {
    trustId: string,
    trustName: string,
    status: CohortSnapshotState,
    error: string | null,
    rowCount: number | null,
    approvedRecordCount: number | null,
    hasAccessions: boolean | null,
    snapshotAt: string | null,
    queryId: string | null,
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

export interface ITrustDecisions {
    approved: string[];
    declined: string[];
}

export interface IImagingDispatchResult {
    trust: string;
    success: boolean;
    message: string;
}

export interface IApproveProjectResponse {
    // APPROVED once every trust has a decision and at least one approved; otherwise the project stays STAGED.
    projectStatus: ProjectStatus;
    // Present once the project is APPROVED: whether imaging started at every approved trust, and per trust.
    successful?: boolean;
    details?: IImagingDispatchResult[];
}

export async function approveProject(url: string, decisions: ITrustDecisions): Promise<IApproveProjectResponse> {
    const response = await _http.post<IApproveProjectResponse>(url, {
        trusts: decisions.approved,
        declined: decisions.declined
    });

    return response.data;
}

export async function deleteProject(url: string): Promise<void> {
    await _http.delete<never>(url);
}

export async function getImagingProjectsStatus(url: string): Promise<IImagingProjectStatus[]> {
    const response = await _http.get<IImagingProjectStatus[]>(url);

    return response.data;
}

export async function getCohortSnapshots(url: string): Promise<ICohortSnapshot[]> {
    const response = await _http.get<ICohortSnapshot[]>(url);

    return response.data;
}
