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

import useSWRV from "swrv";
import { computed } from "vue";

import { getOMOPResults } from "@/services/cohort-query-service";
import { useProjectStore } from "@/store/project";

// Per-trust state comes from `trustRecordCounts` and `trustErrors` on the
// response — both populated by the hub aggregator. A trust present in
// trustRecordCounts has responded successfully (count may be 0 for
// privacy-suppressed); a trust in trustErrors failed; a trust in neither is
// still running. We fall back to per-field aggregation only when neither map
// is present (back-compat with older QueryStats rows that pre-date this fix).
interface IFieldShape {
    results: { trustId: string; data: { count: number }[] }[];
}

interface IResultsShape {
    trustsResults?: IFieldShape[];
    trustRecordCounts?: Record<string, number>;
    trustErrors?: Record<string, string>;
    trustSuppressed?: string[];
}

/**
 * The current project's cohort query results, reduced to per-trust counts, errors and
 * privacy-suppressed trusts. Every caller uses the same SWRV key, and useSWRV dedupes by
 * key, so a second caller is a shared cache hit, not a second fetch.
 */
export function useCohortResults() {
    const projectStore = useProjectStore();

    const { data } = useSWRV(
        () => projectStore.project?.query?.id ? `/cohort/${projectStore.project.query.id}` : "",
        getOMOPResults,
        {
            errorRetryInterval: 1_000,
            refreshInterval: projectStore.project?.status !== "UNSTAGED" ? 0 : 10_000
        }
    );

    const shape = computed(() => (data.value ?? null) as unknown as IResultsShape | null);

    const perTrustCounts = computed<Record<string, number>>(() => {
        const r = shape.value;
        if (r?.trustRecordCounts) return r.trustRecordCounts;
        // Back-compat fallback for QueryStats rows written before the trust_record_counts field existed.
        const trustsResults = r?.trustsResults;
        if (!trustsResults?.length) return {};
        const firstField = trustsResults.find(f => f.results?.length);
        if (!firstField) return {};
        const counts: Record<string, number> = {};
        for (const t of firstField.results) {
            counts[t.trustId] = t.data.reduce((acc, d) => acc + (d.count || 0), 0);
        }

        return counts;
    });

    const perTrustErrors = computed<Record<string, string>>(() => shape.value?.trustErrors ?? {});

    // Trust ids whose count was privacy-suppressed (below threshold). These trusts
    // responded successfully, so the exact count (which may be zero) is hidden. See #519.
    const perTrustSuppressed = computed<Set<string>>(() => new Set(shape.value?.trustSuppressed ?? []));

    return {
        perTrustCounts,
        perTrustErrors,
        perTrustSuppressed
    };
}
