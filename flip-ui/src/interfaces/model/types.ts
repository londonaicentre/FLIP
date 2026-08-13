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




export enum FileUploadStatus {
    // Client-side only: the bytes are still on their way to S3. The server
    // knows nothing about the file until it is registered for scanning.
    UPLOADING = "UPLOADING",
    SCANNING = "SCANNING",
    COMPLETED = "COMPLETED",
    ERROR = "ERROR",
    // The scan judged the file unsafe (e.g. a pickle naming dangerous
    // globals) and deleted it from storage. Distinct from ERROR so the user
    // is told the file was rejected, not that something went wrong.
    INFECTED = "INFECTED",
}

// One Bandit finding for a .py upload (#877). Advisory only — never changes
// FileUploadStatus, so a file with findings can still be COMPLETED.
export interface BanditFinding {
    test_id: string | null;
    issue_text: string | null;
    severity: string | null;
    confidence: string | null;
    line_number: number | null;
}

export interface FileInfo {
    id: string;
    name: string;
    size: number;
    status: FileUploadStatus;
    // Matches the backend's UploadedFiles field name verbatim (this response
    // path serializes that model directly, with no camelCase alias).
    bandit_findings?: BanditFinding[] | null;
}

export interface FileTableRow {
    id: string;
    name: string;
    size: number;
    tag?: string;
    status: FileUploadStatus;
    bandit_findings?: BanditFinding[] | null;
}
