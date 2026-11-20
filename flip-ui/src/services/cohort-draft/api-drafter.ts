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

// The hub's cohort SQL drafting routes (FLIP#1556), mapped onto the drafter interface the
// assistant uses. The hub reads the project's shape itself, so hasImaging is never sent.

import { _http } from "@/services/api";

import type { DraftResult, ICohortDrafter, IDraftResponse, IDraftUsage } from "./types";

/** Model calls plus up to two checked retries take far longer than the client's 30 s default. */
const DRAFT_TIMEOUT_MS = 180_000;

export interface IHubDraftUsage {
    enabled: boolean;
    used: number;
    limit: number;
    remaining: number;
    resets_at: string;
}

export interface IHubDraftResponse {
    status: "draft" | "clarification" | "decline" | "error";
    sql: string | null;
    explanation: string | null;
    assumptions: string[];
    concepts_used: string[];
    question: string | null;
    options: string[];
    error_code: string | null;
    message: string | null;
    usage: IHubDraftUsage;
}

export const toUsage = (u: IHubDraftUsage): IDraftUsage => ({
    enabled: u.enabled,
    limit: u.limit,
    used: u.used,
    resetsAt: u.resets_at
});

/** Maps one hub response; a failure that is not an exhausted allowance becomes a thrown Error. */
export const toResult = (r: IHubDraftResponse, description: string): DraftResult => {
    switch (r.status) {
        case "draft": return {
            status: "draft",
            description,
            sql: r.sql ?? "",
            explanation: r.explanation ?? "",
            assumptions: r.assumptions,
            conceptsUsed: r.concepts_used
        };
        case "clarification": return {
            status: "needs_clarification",
            question: r.question ?? "",
            options: r.options
        };
        case "decline": return {
            status: "declined",
            explanation: r.message ?? ""
        };
        default:
            if (r.error_code === "allowance_exhausted") return { status: "quota_exhausted" };
            throw new Error(r.message ?? "The drafting service is unavailable.");
    }
};

export const createApiDrafter = (projectId: string): ICohortDrafter => ({
    async getUsage() {
        const { data } = await _http.get<IHubDraftUsage>("/cohort/draft/usage");

        return toUsage(data);
    },

    async draft(request, signal): Promise<IDraftResponse> {
        const { data } = await _http.post<IHubDraftResponse>(
            "/cohort/draft",
            {
                project_id: projectId,
                description: request.description,
                clarifications: request.clarifications ?? []
            },
            {
                signal,
                timeout: DRAFT_TIMEOUT_MS,
                // One key per click: a retried request is never charged twice.
                headers: { "Idempotency-Key": crypto.randomUUID() }
            }
        );

        return {
            result: toResult(data, request.description),
            usage: toUsage(data.usage)
        };
    }
});
