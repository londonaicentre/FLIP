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

// Shapes of the FLIP#1556 drafting contract (POST /cohort/draft, GET /cohort/draft/usage),
// as proposed in the issue. Only the fake drafter implements them so far.

/** Server-authoritative monthly allowance. Only a returned draft is charged. */
export interface IDraftUsage {
    limit: number;
    used: number;
    /** ISO timestamp of the next reset: 00:00 UTC on the 1st of next month. */
    resetsAt: string;
}

export interface IDraftRequest {
    description: string;
    /** Picks the output contract: accession_id + person_id (imaging) or person_id only. */
    hasImaging: boolean;
    /** Answers to earlier clarification questions, oldest first. */
    clarifications?: string[];
}

export interface ICohortDraft {
    status: "draft";
    description: string;
    sql: string;
    assumptions: string[];
    conceptsUsed: string[];
    explanation: string;
}

export interface IDraftClarification {
    status: "needs_clarification";
    question: string;
    /** Suggested one-tap answers; a free-text answer is always allowed. */
    options: string[];
}

/** The model declined, or the request is outside what the project shape supports. */
export interface IDraftDeclined {
    status: "declined";
    explanation: string;
}

export interface IDraftQuotaExhausted {
    status: "quota_exhausted";
}

export type DraftResult = ICohortDraft | IDraftClarification | IDraftDeclined | IDraftQuotaExhausted;

export interface IDraftResponse {
    result: DraftResult;
    usage: IDraftUsage;
}

export interface ICohortDrafter {
    getUsage(): Promise<IDraftUsage>;
    /** Rejects on failure or when `signal` aborts. Never runs SQL. */
    draft(request: IDraftRequest, signal: AbortSignal): Promise<IDraftResponse>;
}
