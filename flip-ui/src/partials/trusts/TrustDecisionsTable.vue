<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<!-- The Trust Admin's decided projects (FLIP#1258, design_handoff_my_trust): a table whose rows carry a rail and a
     pill in the decision's tone — the Models page's language — and expand to the description and a link to the
     project's cohort query. -->
<template>
    <div
        data-test="decided-list"
        class="overflow-hidden bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
    >
        <div class="hidden sm:flex items-stretch bg-gray-50 border-b border-gray-200 dark:bg-dark-surface dark:border-dark-border">
            <div class="w-[3px] shrink-0" aria-hidden="true" />
            <div :class="GRID_CLASS" class="grid flex-1 gap-4 px-6 py-2.5 font-mono text-[11px] uppercase tracking-[0.1em] text-gray-500 dark:text-gray-300">
                <span>Project</span>
                <span>Cohort</span>
                <span>Decision</span>
                <span />
            </div>
        </div>

        <div
            v-for="decision in decisions"
            :key="decision.projectId"
            data-test="decision-row"
            :data-project="decision.projectId"
            class="border-t border-gray-100 first:border-t-0 dark:border-dark-border"
        >
            <button
                type="button"
                data-test="decision-row-toggle"
                class="flex items-stretch w-full text-left transition-colors hover:bg-gray-50 dark:hover:bg-dark-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                :aria-expanded="isOpen(decision)"
                @click="toggle(decision)"
            >
                <span data-test="decision-rail" class="w-[3px] shrink-0" :class="toneOf(decision).rail" aria-hidden="true" />
                <span :class="GRID_CLASS" class="grid flex-1 items-center gap-4 px-6 py-3.5">
                    <span class="min-w-0">
                        <span class="block text-sm font-bold truncate text-primary-600 dark:text-primary-200">
                            {{ decision.projectName }}
                        </span>
                        <span class="block mt-0.5 text-xs text-gray-500 truncate dark:text-gray-300">
                            {{ decision.ownerName ?? "Unknown owner" }}<template v-if="decision.stagedAt"> · staged {{ shortDate(decision.stagedAt) }}</template>
                        </span>
                    </span>
                    <span class="text-[13px] text-gray-700 dark:text-gray-200">
                        <b class="font-semibold text-gray-900 dark:text-gray-100">{{ cohortSummary(decision.cohort).value }}</b>
                        {{ cohortSummary(decision.cohort).note }} · {{ decision.hasImaging ? "imaging" : "tabular" }}
                    </span>
                    <span class="min-w-0">
                        <span
                            data-test="decision-pill"
                            class="inline-flex items-center gap-1.5 px-2.5 py-[3px] text-xs font-medium rounded-full"
                            :class="toneOf(decision).pill"
                        >
                            <span class="w-1.5 h-1.5 rounded-full" :class="toneOf(decision).rail" aria-hidden="true" />
                            {{ toneOf(decision).label }}
                        </span>
                        <span class="block mt-1 text-[11.5px] text-gray-500 dark:text-gray-300">
                            {{ decidedBy(decision) }}
                        </span>
                    </span>
                    <icon-heroicons-outline-chevron-down
                        class="w-[18px] h-[18px] text-gray-400 dark:text-gray-300 transition-transform duration-150 ease-in-out justify-self-end"
                        :class="isOpen(decision) && 'rotate-180'"
                        aria-hidden="true"
                    />
                </span>
            </button>
            <div v-if="isOpen(decision)" data-test="decision-details" class="pb-[18px] pl-[27px] pr-6 space-y-2">
                <p v-if="decision.description" class="text-sm text-gray-700 dark:text-gray-200">
                    {{ decision.description }}
                </p>
                <p class="text-[13px] text-gray-500 dark:text-gray-300">
                    Imaging:
                    {{ decision.hasImaging ? "Yes — approving starts the imaging pull" : "No — tabular data only" }}
                </p>
                <router-link
                    v-if="decision.query"
                    data-test="view-query-btn"
                    :to="`/project/${decision.projectId}/cohort-query`"
                    class="inline-flex items-center gap-1.5 px-2 py-1.5 -ml-2 text-[13.5px] font-semibold rounded text-primary-500 hover:bg-primary-100 dark:text-primary-300 dark:hover:bg-dark-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
                >
                    View query
                </router-link>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

import type { ITrustDecision } from "@/services/trust-service";
import { closedWithoutDecision, cohortSummary, shortDate } from "@/utils/trust-decisions";

defineProps<{ decisions: ITrustDecision[] }>();

const GRID_CLASS = "grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.1fr)_28px]";

// The Models page's status tones (model-service.ts): emerald approved, red declined, grey for a trust closed at
// upgrade, which nobody declined.
const TONES: Record<ITrustDecision["status"] | "CLOSED", { label: string; pill: string; rail: string }> = {
    APPROVED: {
        label: "Approved",
        pill: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
        rail: "bg-emerald-500"
    },
    DECLINED: {
        label: "Declined",
        pill: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
        rail: "bg-red-500"
    },
    PENDING: {
        label: "Awaiting decision",
        pill: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
        rail: "bg-amber-500"
    },
    CLOSED: {
        label: "Not approved",
        pill: "bg-gray-100 text-gray-700 dark:bg-gray-800/60 dark:text-gray-200",
        rail: "bg-gray-400"
    }
};
const toneOf = (decision: ITrustDecision) => TONES[closedWithoutDecision(decision) ? "CLOSED" : decision.status];

const open = ref<Set<string>>(new Set());
const isOpen = (decision: ITrustDecision) => open.value.has(decision.projectId);
const toggle = (decision: ITrustDecision) => {
    const next = new Set(open.value);
    if (next.has(decision.projectId)) next.delete(decision.projectId);
    else next.add(decision.projectId);
    open.value = next;
};

// "Ada Admin · 2 Sep 2026", marked "(hub)" when the hub decided before the trust had a Trust Admin.
const decidedBy = (decision: ITrustDecision): string => {
    if (closedWithoutDecision(decision)) return "No decision recorded";
    const who = decision.decidedByName ? `${decision.decidedByName}${decision.decidedAs === "HUB" ? " (hub)" : ""}` : "";

    return [who, decision.decidedAt ? shortDate(decision.decidedAt) : ""].filter(Boolean).join(" · ");
};
</script>
