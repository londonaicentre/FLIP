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

<route lang="yaml">
    name: My Trust
</route>

<!-- A Trust Admin's page (FLIP#1258, design_handoff_my_trust): the projects staged at their trust — awaiting their
     decision, then decided — beside the trust itself as Connection Status shows it. -->
<template>
    <div class="flex flex-col w-full h-full">
        <div class="w-full overflow-y-auto">
            <div class="max-w-[1440px] mx-auto px-8 pt-8 pb-12">
                <!-- Header — the Models page's spine: mono eyebrow, underlined title, description. -->
                <header class="mb-6">
                    <p
                        data-test="my-trust-eyebrow"
                        class="text-[11px] font-mono uppercase tracking-[0.16em] text-gray-500 dark:text-gray-300"
                    >
                        Trust admin · {{ trustAdminOf?.code ?? trustAdminOf?.name }}
                    </p>
                    <h1 class="mt-1.5 text-[28px] font-semibold font-heading">
                        <span class="text-primary-500 underline decoration-4 decoration-primary-400/60 underline-offset-8 dark:text-white">My Trust</span>
                    </h1>
                    <p data-test="my-trust-description" class="mt-3.5 max-w-[620px] text-sm text-gray-500 dark:text-gray-300">
                        Review project requests to use data held at
                        <strong data-test="my-trust-name" class="font-semibold text-gray-900 dark:text-gray-100">{{ trustName }}</strong>,
                        and monitor the health of the local FLIP node.
                    </p>
                </header>

                <!-- Decisions first, the page's job; the trust's own card to their right, wrapping below them on
                     narrow screens. -->
                <div class="flex flex-wrap items-start gap-6">
                    <div class="flex-[1_1_560px] min-w-0 space-y-8">
                        <section>
                            <h2
                                data-test="pending-heading"
                                class="flex items-baseline gap-2.5 mb-3 text-lg font-bold text-gray-900 font-heading dark:text-gray-100"
                            >
                                Awaiting your decision
                                <span data-test="pending-count" class="font-medium text-gray-400 dark:text-gray-300">{{ pending.length }}</span>
                            </h2>
                            <div
                                v-if="decisionsError"
                                data-test="decisions-error"
                                class="px-6 py-5 text-sm text-red-700 bg-white border border-red-200 rounded-xl dark:bg-dark-canvas dark:border-red-800 dark:text-red-300"
                            >
                                This Trust's project requests could not be loaded. Reload the page to try again.
                            </div>
                            <div
                                v-else-if="!decisions"
                                data-test="decisions-loading"
                                class="py-10 bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
                            >
                                <AiLoader />
                            </div>
                            <div
                                v-else-if="!pending.length"
                                data-test="nothing-pending"
                                class="flex flex-col items-center justify-center gap-2.5 px-6 py-10 text-center bg-white border border-dashed border-gray-300 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
                            >
                                <icon-ph-archive-duotone class="w-9 h-9 text-primary-400" aria-hidden="true" />
                                <p class="text-sm font-semibold text-gray-700 dark:text-gray-100">
                                    There are no requests awaiting your decision
                                </p>
                                <p class="text-[13px] text-gray-500 dark:text-gray-300">
                                    New project requests for this Trust will appear here.
                                </p>
                            </div>
                            <div v-else data-test="pending-list" class="space-y-3">
                                <TrustRequestCard
                                    v-for="decision in pending"
                                    :key="decision.projectId"
                                    :decision="decision"
                                    :busy="submitting"
                                    @decide="askToDecide(decision, $event)"
                                />
                            </div>
                        </section>

                        <section>
                            <h2 class="flex items-baseline gap-2.5 mb-3 text-lg font-bold text-gray-900 font-heading dark:text-gray-100">
                                Decided
                                <span data-test="decided-count" class="font-medium text-gray-400 dark:text-gray-300">{{ decided.length }}</span>
                            </h2>
                            <p
                                v-if="decisions && !decided.length"
                                class="px-6 py-5 text-sm text-gray-500 bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border dark:text-gray-300"
                            >
                                No decisions yet.
                            </p>
                            <TrustDecisionsTable v-else-if="decided.length" :decisions="decided" />
                        </section>
                    </div>

                    <div class="flex-[1_1_340px] max-w-[420px] min-w-0">
                        <AiCard data-test="trust-card-column">
                            <TrustDetailCard v-if="derivedTrust" :trust="derivedTrust" :hub-version="hubVersion" />
                            <div v-else class="p-6">
                                <AiLoader />
                            </div>
                        </AiCard>
                    </div>
                </div>
            </div>
        </div>

        <AiConfirmModal
            :dialog="!!choice"
            :submitting="submitting"
            :title="choice?.approve ? 'Approve project' : 'Decline project'"
            :confirmation-text="confirmationText"
            :continue-button-text="choice?.approve ? 'Approve' : 'Decline'"
            :continue-action="confirmDecision"
            @close-modal="choice = null"
        />
    </div>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, onBeforeMount, ref } from "vue";

import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import TrustDetailCard from "@/partials/connection/TrustDetailCard.vue";
import TrustDecisionsTable from "@/partials/trusts/TrustDecisionsTable.vue";
import TrustRequestCard from "@/partials/trusts/TrustRequestCard.vue";
import { routeChange } from "@/router";
import { approveProject } from "@/services/project-service";
import { getHubHealth,
    getTrustDecisions,
    getTrustStatuses,
    IHubHealth,
    ITrustDecision,
    ITrustResponse } from "@/services/trust-service";
import { useAuthStore } from "@/store/auth";
import { extractErrorDetail } from "@/utils/api-errors";
import { deriveTrust } from "@/utils/connection-health";
import { Snackbar } from "@/utils/snackbar";

const authStore = useAuthStore();
const trustAdminOf = computed(() => authStore.trustAdminOf);

onBeforeMount(() => {
    if (!trustAdminOf.value) routeChange.viewProjects();
});

// Same key and cadence as Connection Status, so the two pages share one poll.
const { data: trusts } = useSWRV<ITrustResponse[]>("trust-connection-status", getTrustStatuses, {
    dedupingInterval: 5_000,
    shouldRetryOnError: false,
    refreshInterval: 15_000
});
const { data: hubHealth } = useSWRV<IHubHealth>("hub-health", getHubHealth, {
    dedupingInterval: 30_000,
    shouldRetryOnError: false,
    refreshInterval: 60_000
});
const hubVersion = computed<string | null>(() => hubHealth.value?.version ?? null);

const derivedTrust = computed(() => {
    const trust = (trusts.value ?? []).find(t => t.id === trustAdminOf.value?.id);

    return trust ? deriveTrust(trust) : null;
});
const trustName = computed(() => trustAdminOf.value?.name ?? "your trust");

// The header's pending badge uses this key too, so a decision here updates it.
const { data: decisions, error: decisionsError, mutate: refreshDecisions } = useSWRV<ITrustDecision[]>(
    () => (trustAdminOf.value ? `/trust/${trustAdminOf.value.id}/decisions` : null),
    () => getTrustDecisions(trustAdminOf.value!.id),
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);
const pending = computed(() => (decisions.value ?? []).filter(d => d.status === "PENDING"));
const decided = computed(() => (decisions.value ?? []).filter(d => d.status !== "PENDING"));

const choice = ref<{ decision: ITrustDecision; approve: boolean } | null>(null);
const submitting = ref(false);

const askToDecide = (decision: ITrustDecision, what: "approve" | "decline") => {
    choice.value = {
        decision,
        approve: what === "approve"
    };
};

const confirmationText = computed(() => {
    if (!choice.value) return "";
    const { decision, approve } = choice.value;
    if (!approve) {
        return `Decline ${decision.projectName} for ${trustName.value}? The project will not use ${trustName.value}'s data.`;
    }
    const effect = decision.hasImaging
        ? `This starts the imaging pull at ${trustName.value}.`
        : `The project can then use ${trustName.value}'s data.`;

    return `Approve ${decision.projectName} for ${trustName.value}? ${effect}`;
});

const confirmDecision = async () => {
    if (!choice.value || !trustAdminOf.value) return;
    const { decision, approve } = choice.value;
    const trustId = trustAdminOf.value.id;
    submitting.value = true;
    try {
        const response = await approveProject(`/step/project/${decision.projectId}/approve`, {
            approved: approve ? [trustId] : [],
            declined: approve ? [] : [trustId]
        });
        // The approval is committed even when imaging could not be started, so this is a warning, not an error.
        const failed = (response?.details ?? []).filter(d => !d.success).map(d => d.trust);
        if (approve && response?.successful === false && failed.length) {
            Snackbar.warning({
                title: "Project approved, imaging not started",
                text: `${decision.projectName} was approved for ${trustName.value}, but imaging could not be started. `
                    + "Ask a FLIP administrator to check the trust's connection."
            });
        }
        else {
            Snackbar.success({
                title: approve ? "Project approved" : "Project declined",
                text: `${decision.projectName} was ${approve ? "approved" : "declined"} for ${trustName.value}.`
            });
        }
        await refreshDecisions();
    } catch (e) {
        Snackbar.error({
            title: "Decision not saved",
            text: extractErrorDetail(e, "The decision could not be saved, please try again.")
        });
    } finally {
        submitting.value = false;
        choice.value = null;
    }
};
</script>
