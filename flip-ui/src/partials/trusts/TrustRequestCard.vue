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

<!-- A project awaiting the Trust Admin's decision (FLIP#1258, design_handoff_my_trust): an amber-railed card with
     the owner and staging date, the facts the decision needs, a link to the project's cohort query, and
     Approve / Decline. The page owns the confirmation and the call. -->
<template>
    <article
        data-test="request-card"
        :data-project="decision.projectId"
        class="flex overflow-hidden bg-white border border-gray-200 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.04)] dark:bg-dark-canvas dark:border-dark-border"
    >
        <div class="w-[3px] shrink-0 bg-amber-500" aria-hidden="true" />
        <div class="flex-1 min-w-0">
            <div class="px-6 py-5">
                <div class="flex flex-wrap items-start justify-between gap-3">
                    <div class="min-w-0">
                        <h3 class="text-lg font-semibold text-gray-900 font-heading dark:text-gray-100">
                            {{ decision.projectName }}
                        </h3>
                        <p class="mt-1 text-[12.5px] text-gray-500 dark:text-gray-300">
                            {{ decision.ownerName ?? "Unknown owner" }}<template v-if="decision.stagedAt">
                                · staged {{ shortDate(decision.stagedAt) }}
                            </template>
                        </p>
                    </div>
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                        <span class="w-1.5 h-1.5 rounded-full bg-amber-500" aria-hidden="true" />
                        Awaiting decision
                    </span>
                </div>

                <p v-if="decision.description" class="mt-3.5 text-sm leading-[1.55] text-gray-700 dark:text-gray-200">
                    {{ decision.description }}
                </p>

                <div class="grid gap-3 mt-4 grid-cols-[repeat(auto-fit,minmax(200px,1fr))]">
                    <div class="px-4 py-3 border rounded-lg bg-gray-50 border-gray-100 dark:bg-dark-surface dark:border-dark-border">
                        <p class="font-mono text-[10.5px] uppercase tracking-[0.12em] text-gray-500 dark:text-gray-300">
                            Cohort at this Trust
                        </p>
                        <p class="mt-1">
                            <span data-test="request-cohort" class="text-2xl font-bold text-gray-900 font-heading dark:text-gray-100">{{ cohort.value }}</span>
                            <span data-test="request-cohort-note" class="ml-1.5 text-[13px] text-gray-500 dark:text-gray-300">{{ cohort.note }}</span>
                        </p>
                    </div>
                    <div class="px-4 py-3 border rounded-lg bg-gray-50 border-gray-100 dark:bg-dark-surface dark:border-dark-border">
                        <p class="font-mono text-[10.5px] uppercase tracking-[0.12em] text-gray-500 dark:text-gray-300">
                            Imaging
                        </p>
                        <p class="mt-1">
                            <span data-test="request-imaging" class="text-2xl font-bold text-gray-900 font-heading dark:text-gray-100">{{ decision.hasImaging ? "Yes" : "No" }}</span>
                            <span class="ml-1.5 text-[13px] text-gray-500 dark:text-gray-300">{{ decision.hasImaging ? "Imaging studies requested" : "Tabular data only" }}</span>
                        </p>
                    </div>
                </div>

                <p
                    v-if="decision.hasImaging"
                    data-test="imaging-notice"
                    class="flex items-center gap-2 px-3 py-2 mt-3 text-[13px] rounded-md bg-steel-100 text-[#1E2B4D] dark:bg-steel-700/30 dark:text-gray-100"
                >
                    <icon-heroicons-outline-information-circle class="w-4 h-4 shrink-0 text-[#4F7A9B]" aria-hidden="true" />
                    Approving starts the imaging pull from PACS to XNAT.
                </p>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-2 px-6 py-3 border-t border-gray-100 bg-[#FCFBFC] dark:bg-dark-surface dark:border-dark-border">
                <router-link
                    v-if="decision.query"
                    data-test="view-query-btn"
                    :to="`/project/${decision.projectId}/cohort-query`"
                    class="inline-flex items-center gap-1.5 px-2 py-1.5 text-[13.5px] font-semibold rounded text-primary-500 hover:bg-primary-100 dark:text-primary-300 dark:hover:bg-dark-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
                >
                    View query
                </router-link>
                <span v-else class="text-[13px] text-gray-500 dark:text-gray-300">No cohort query</span>
                <div class="flex gap-2 ml-auto">
                    <AiButton data-test="decline-btn" :disabled="busy" @click="emit('decide', 'decline')">
                        Decline
                    </AiButton>
                    <AiButton data-test="approve-btn" primary :disabled="busy" @click="emit('decide', 'approve')">
                        Approve
                    </AiButton>
                </div>
            </div>
        </div>
    </article>
</template>

<script setup lang="ts">
import { computed } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import type { ITrustDecision } from "@/services/trust-service";
import { cohortSummary, shortDate } from "@/utils/trust-decisions";

const props = withDefaults(defineProps<{
    decision: ITrustDecision;
    busy?: boolean;
}>(), { busy: false });

const emit = defineEmits<{ decide: [choice: "approve" | "decline"] }>();

const cohort = computed(() => cohortSummary(props.decision.cohort));
</script>
