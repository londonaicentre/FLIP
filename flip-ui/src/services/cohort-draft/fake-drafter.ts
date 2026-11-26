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

// Fake drafter for the FLIP#1556 UI mock: canned responses after a realistic delay, no network
// and no model. The response is picked from keywords in the description (and any clarification
// answers); `?draftScenario=draft|clarify|decline|error|exhausted` in the page URL forces one.

import { CHEST_XRAY_DRAFT,
    CT_WOMEN_OVER_60_DRAFT,
    DELETE_AND_IDENTIFIERS_DECLINE,
    MRI_WOMEN_OVER_60_DRAFT,
    T2DM_TABULAR_DRAFT } from "./canned-drafts";
import type { DraftResult, ICohortDrafter, IDraftClarification, IDraftRequest, IDraftResponse, IDraftUsage } from "./types";

export type DraftScenario = "draft" | "clarify" | "decline" | "error" | "exhausted";

const MONTHLY_LIMIT = 30;

const TABULAR_IMAGING_DECLINE =
    "This is a tabular-only project, so a draft can return person_id only and cannot select imaging "
    + "studies. Describe the cohort by conditions, measurements, drugs, visits or demographics instead.";

const MODALITY_QUESTION: IDraftClarification = {
    status: "needs_clarification",
    question: "Which modality: CT or MRI?",
    options: ["CT", "MRI"]
};

/** 00:00 UTC on the 1st of the month after `now`. */
export const nextMonthlyReset = (now: Date): string =>
    new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)).toISOString();

const has = (text: string, pattern: RegExp): boolean => pattern.test(text);

/** Picks the canned response for a request. Pure, so the mapping can be tested directly. */
export const pickResult = (request: IDraftRequest, scenario?: DraftScenario): DraftResult | "error" => {
    const text = [request.description, ...(request.clarifications ?? []).map((c) => c.answer)].join(" ").toLowerCase();
    const shapeDraft = request.hasImaging ? CHEST_XRAY_DRAFT : T2DM_TABULAR_DRAFT;

    switch (scenario) {
        case "draft": return shapeDraft;
        case "clarify":
            if (!request.clarifications?.length) return MODALITY_QUESTION;
            break;
        case "decline": return {
            status: "declined",
            explanation: DELETE_AND_IDENTIFIERS_DECLINE.explanation
        };
        case "error": return "error";
        default: break;
    }

    if (has(text, /\b(delete|drop|update|insert|names?|nhs numbers?)\b/)) {
        return {
            status: "declined",
            explanation: DELETE_AND_IDENTIFIERS_DECLINE.explanation
        };
    }
    if (has(text, /\b(fail|failure|timeout|offline)\b/)) return "error";

    if (!request.hasImaging) {
        return has(text, /x-?rays?|\bct\b|\bmri\b|\bscans?\b/)
            ? {
                status: "declined",
                explanation: TABULAR_IMAGING_DECLINE
            }
            : T2DM_TABULAR_DRAFT;
    }

    if (has(text, /\bmri\b/)) return MRI_WOMEN_OVER_60_DRAFT;
    if (has(text, /\bct\b/)) return CT_WOMEN_OVER_60_DRAFT;
    if (has(text, /x-?rays?|chest|pneumonia/)) return CHEST_XRAY_DRAFT;

    // An imaging cohort with no modality named: ask rather than guess.
    return MODALITY_QUESTION;
};

const wait = (ms: number, signal: AbortSignal): Promise<void> => new Promise((resolve, reject) => {
    const abort = () => {
        clearTimeout(timer);
        reject(new DOMException("Drafting cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
    }, ms);

    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
});

interface IFakeDrafterOptions {
    scenario?: DraftScenario;
    used?: number;
    /** Drafting delay in ms; defaults to a random 1.5–2.5 s. */
    delay?: () => number;
    now?: () => Date;
}

export const createFakeDrafter = (options: IFakeDrafterOptions = {}): ICohortDrafter => {
    const { scenario, delay = () => 1_500 + Math.random() * 1_000, now = () => new Date() } = options;
    let used = scenario === "exhausted" ? MONTHLY_LIMIT : Math.min(options.used ?? 12, MONTHLY_LIMIT);

    const usage = (): IDraftUsage => ({
        enabled: true,
        limit: MONTHLY_LIMIT,
        used,
        resetsAt: nextMonthlyReset(now())
    });

    return {
        getUsage: async () => usage(),

        async draft(request, signal): Promise<IDraftResponse> {
            await wait(delay(), signal);

            // Checked after the wait, as the server would: another session may have used the rest.
            if (used >= MONTHLY_LIMIT) return {
                result: { status: "quota_exhausted" },
                usage: usage()
            };

            const result = pickResult(request, scenario);
            if (result === "error") throw new Error("The drafting service did not respond in time.");

            // Only a returned draft is charged; clarifications and declines are free.
            if (result.status === "draft") used += 1;

            return {
                result,
                usage: usage()
            };
        }
    };
};

/** A drafter configured from `?draftScenario=` and `?draftUsed=` (dev only). */
export const drafterFromLocation = (search: string): ICohortDrafter => {
    const params = new URLSearchParams(search);
    const used = Number(params.get("draftUsed"));

    return createFakeDrafter({
        scenario: (params.get("draftScenario") ?? undefined) as DraftScenario | undefined,
        used: Number.isFinite(used) && params.has("draftUsed") ? used : undefined
    });
};

let pageDrafter: ICohortDrafter | undefined;

/** One drafter per page load, so the allowance carries across navigation like a server's would. */
export const getPageDrafter = (): ICohortDrafter => (pageDrafter ??= drafterFromLocation(window.location.search));
