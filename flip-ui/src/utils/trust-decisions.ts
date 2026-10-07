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

import type { ITrustCohortCount } from "@/services/trust-service";

/**
 * A trust closed at upgrade (FLIP#1258): pending on a project approved before trusts decided for themselves,
 * so it was closed as DECLINED with no decider and no date. Nobody declined it; it reads "Not approved".
 */
export const closedWithoutDecision = (decision: {
    status: string;
    decidedByName?: string | null;
    decidedAt?: string | null;
}): boolean => decision.status === "DECLINED" && !decision.decidedByName && !decision.decidedAt;

/** "25 Sep 2026" — the My Trust page's date format (FLIP#1258). */
export const shortDate = (iso: string): string =>
    new Date(iso).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric"
    });

/**
 * A trust's cohort count as a value and a note: "812" "records", or a dash with why there is no number.
 *
 * Args:
 *     cohort (ITrustCohortCount | null): The trust's latest answer to the cohort query, if any.
 *
 * Returns:
 *     { value: string; note: string }: What to show, and the unit or reason beside it.
 */
export function cohortSummary(cohort: ITrustCohortCount | null): { value: string; note: string } {
    if (!cohort) return {
        value: "—",
        note: "not reported"
    };
    if (cohort.error) return {
        value: "—",
        note: "query failed"
    };
    if (cohort.suppressed) return {
        value: "—",
        note: "below the disclosure threshold"
    };

    return {
        value: (cohort.recordCount ?? 0).toLocaleString("en-GB"),
        note: "records"
    };
}
