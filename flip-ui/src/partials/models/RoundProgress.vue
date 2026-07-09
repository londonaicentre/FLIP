<!--
  Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

<!-- Federated round card (design handoff 03c). A pure renderer of the
     server-derived GET /model/{id}/progress view: round position, segment
     strip and the collapsible per-trust ladder. All round math lives in
     flip-api; metric values live in the metrics card below, which knows each
     series' label and axis. -->
<template>
    <AiCard v-if="visible" data-test="round-progress-card">
        <div class="px-5 py-4 sm:px-6 grid grid-cols-1 lg:grid-cols-[auto_1fr] gap-5 lg:gap-7 items-center">
            <!-- Round count -->
            <div>
                <div class="flex items-center gap-2">
                    <span
                        v-if="live"
                        class="relative flex h-2.5 w-2.5"
                        aria-hidden="true"
                    >
                        <span
                            data-test="round-live-dot"
                            class="animate-ping absolute inline-flex h-full w-full rounded-full bg-fuchsia-500 opacity-60"
                        />
                        <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-fuchsia-500" />
                    </span>
                    <span class="font-mono text-[11px] uppercase tracking-[0.12em] text-gray-500 dark:text-gray-300">
                        Federated round
                    </span>
                </div>
                <div class="mt-1 flex items-baseline gap-1.5">
                    <span
                        data-test="round-current"
                        class="font-heading text-[34px] font-bold leading-none text-gray-900 dark:text-gray-100"
                    >{{ currentRound }}</span>
                    <span data-test="round-total" class="font-heading text-lg text-gray-500 dark:text-gray-300">
                        / {{ totalRoundsLabel }}
                    </span>
                </div>
            </div>

            <!-- Segment strip + timing meta -->
            <div class="min-w-0">
                <div class="flex flex-wrap justify-between gap-x-4 mb-2">
                    <span
                        data-test="round-meta-left"
                        class="font-mono text-[11px] text-gray-500 dark:text-gray-300"
                    >{{ timingMeta }}</span>
                    <span
                        data-test="round-meta-right"
                        class="font-mono text-[11px] text-gray-500 dark:text-gray-300"
                    >{{ returnSummary }}</span>
                </div>
                <div v-if="segments.length" class="flex items-center gap-1 h-4">
                    <div
                        v-for="segment in segments"
                        :key="segment.round"
                        data-test="round-segment"
                        :data-state="segment.state"
                        class="flex-1 rounded-[3px]"
                        :class="segmentClass(segment.state)"
                    />
                </div>
            </div>
        </div>

        <!-- Per-trust ladder toggle -->
        <button
            v-if="progress?.trusts.length"
            type="button"
            data-test="ladder-toggle"
            class="w-full flex items-center gap-2.5 px-5 sm:px-6 py-2 border-t border-gray-100 dark:border-dark-border
                   text-left cursor-pointer select-none"
            :aria-expanded="showLadder"
            @click="showLadder = !showLadder"
        >
            <span
                class="font-mono text-[10.5px] text-gray-500 dark:text-gray-300 transition-transform duration-150"
                :class="{ 'rotate-90': showLadder }"
                aria-hidden="true"
            >▸</span>
            <span class="font-mono text-[11px] uppercase tracking-[0.12em] text-gray-700 dark:text-gray-300">
                Per-trust round position
            </span>
            <span class="hidden sm:inline font-mono text-[10.5px] text-gray-400 dark:text-gray-300 truncate">
                {{ ladderSummary }}
            </span>
            <span class="ml-auto font-mono text-[10.5px] text-gray-400 dark:text-gray-300">
                {{ showLadder ? "hide" : "show" }}
            </span>
        </button>

        <!-- Per-trust ladder -->
        <div
            v-if="showLadder"
            data-test="ladder-panel"
            class="border-t border-gray-100 dark:border-dark-border px-5 sm:px-6 pt-3.5 pb-4"
        >
            <div class="flex flex-col gap-2.5">
                <div
                    v-for="trust in progress?.trusts ?? []"
                    :key="trust.id"
                    data-test="ladder-row"
                    class="grid grid-cols-[minmax(7rem,auto)_1fr] sm:grid-cols-[9.5rem_1fr_auto] gap-x-4 gap-y-1
                           items-center"
                >
                    <div class="flex items-center gap-2 min-w-0">
                        <span
                            class="h-[7px] w-[7px] rounded-full shrink-0"
                            :class="trustDotClass(trust.state)"
                            aria-hidden="true"
                        />
                        <span
                            class="font-mono text-xs font-bold text-gray-900 dark:text-gray-100"
                            :title="trust.name"
                        >{{ trust.code ?? trust.name }}</span>
                        <span
                            v-if="trust.avgRoundSeconds != null"
                            class="font-mono text-[10px] text-gray-400 dark:text-gray-300"
                        >~{{ formatDurationShort(trust.avgRoundSeconds) }}</span>
                    </div>
                    <div v-if="segments.length" class="flex gap-[3px] col-span-2 sm:col-span-1 sm:order-none order-3">
                        <div
                            v-for="segment in segments"
                            :key="segment.round"
                            class="flex-1 h-2.5 rounded-[3px]"
                            :class="trustSegmentClass(trust, segment.round)"
                        />
                    </div>
                    <div class="flex justify-end">
                        <span
                            data-test="ladder-pill"
                            class="inline-flex items-center rounded-full px-2.5 py-0.5 font-mono text-[11px]
                                   font-semibold whitespace-nowrap"
                            :class="trustPillClass(trust.state)"
                        >{{ trustPillText(trust) }}</span>
                    </div>
                </div>
            </div>
            <p class="mt-2.5 text-right font-mono text-[10.5px] text-gray-400 dark:text-gray-300">
                rounds advance with the slowest trust — returned trusts wait for aggregation
            </p>
        </div>
    </AiCard>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, onBeforeUnmount, ref } from "vue";
import { useRoute } from "vue-router";

import AiCard from "@/components/AiCard/AiCard.vue";
import { getModelProgress,
    getStatusEnumValue,
    type IModelProgressTrust,
    type ModelStatus,
    ModelStatusEnum } from "@/services/model-service";
import { formatDurationShort } from "@/utils/helpers";

const props = defineProps<{
    status?: ModelStatus | string;
}>();

const { params } = useRoute();

const statusEnum = computed(() => getStatusEnumValue(props.status as ModelStatus));
const finished = computed(() =>
    [
        ModelStatusEnum.ERROR,
        ModelStatusEnum.RESULTS_UPLOADED,
        ModelStatusEnum.RESULTS_UPLOAD_FAILED,
        ModelStatusEnum.STOPPED
    ].includes(statusEnum.value)
);
const live = computed(() => !finished.value);

// Same 5s cadence as TrainingMetrics/Timeline. The card deliberately shows no
// metric values: metric labels are chosen freely by the training code, so any
// "headline Loss/Accuracy" would have to guess semantics from label substrings
// (and mis-format e.g. a VAL_DICE_LOSS as a percentage). The metrics card below
// renders every series with its own tab and axis.
const { data: progress } = useSWRV(`/model/${params.modelId}/progress`, getModelProgress, {
    refreshInterval: finished.value ? 0 : 5_000,
    dedupingInterval: 5_000,
    shouldRetryOnError: true,
    revalidateOnFocus: false,
    errorRetryCount: 3
});

// PENDING has nothing to show; a model with no round telemetry at all (e.g.
// trained before events existed) hides the card rather than rendering shells.
const visible = computed(
    () =>
        statusEnum.value !== ModelStatusEnum.PENDING &&
        !!progress.value &&
        (progress.value.currentRound !== null || progress.value.trusts.length > 0)
);

const currentRound = computed(() => progress.value?.currentRound ?? 0);
const totalRoundsLabel = computed(() => progress.value?.totalRounds ?? "—");

interface Segment {
    round: number;
    state: "done" | "current" | "upcoming";
}

const segments = computed<Segment[]>(() => {
    const total = progress.value?.totalRounds;
    if (!total) return [];

    return Array.from({ length: total }, (_, i) => {
        const round = i + 1;
        // A finished run keeps no "live" segment — every reached round reads done.
        if (round < currentRound.value || (round === currentRound.value && finished.value)) {
            return {
                round,
                state: "done" as const
            };
        }
        if (round === currentRound.value) return {
            round,
            state: "current" as const
        };

        return {
            round,
            state: "upcoming" as const
        };
    });
});

function segmentClass(state: Segment["state"]): string {
    if (state === "done") return "h-2.5 bg-primary-500";
    if (state === "current") return "h-3.5 bg-fuchsia-500 ring-[3px] ring-fuchsia-500/20";

    return "h-1.5 bg-gray-200 dark:bg-dark-raised";
}

// 30s tick drives the elapsed label; everything else re-renders on poll data.
const now = ref(Date.now());
const ticker = setInterval(() => (now.value = Date.now()), 30_000);
onBeforeUnmount(() => clearInterval(ticker));

const timingMeta = computed(() => {
    const parts: string[] = [];
    const startedAt = progress.value?.startedAt;
    if (startedAt && live.value) {
        parts.push(`elapsed ${formatDurationShort((now.value - new Date(startedAt).getTime()) / 1000)}`);
    }
    if (progress.value?.avgRoundSeconds != null) {
        parts.push(`avg round ${formatDurationShort(progress.value.avgRoundSeconds)}`);
    }
    if (live.value && progress.value?.estRemainingSeconds != null) {
        parts.push(`est. remaining ~${formatDurationShort(progress.value.estRemainingSeconds)}`);
    }

    return parts.join(" · ");
});

const returnSummary = computed(() => {
    const trusts = progress.value?.trusts ?? [];
    if (!trusts.length || progress.value?.currentRound == null) return "";

    const active = trusts.filter((t) => t.state !== "dropped");
    const returned = active.filter((t) => t.state === "returned").length;
    const parts = [`${returned} of ${active.length} returned`];
    if (live.value) {
        const waiting = active.filter((t) => t.state === "training").map((t) => t.code ?? t.name);
        if (waiting.length) parts.push(`waiting on ${waiting.join(", ")}`);
    }

    return parts.join(" · ");
});

const ladderSummary = computed(() => {
    const trusts = progress.value?.trusts ?? [];
    const parts: string[] = [];
    if (live.value) {
        parts.push(...trusts.filter((t) => t.state === "training").map((t) => `${t.code ?? t.name} gating`));
    }
    parts.push(
        ...trusts
            .filter((t) => t.state === "dropped")
            .map((t) => `${t.code ?? t.name} out${t.lastRound != null ? ` since r${t.lastRound}` : ""}`)
    );

    return parts.join(" · ");
});

const showLadder = ref(false);

function trustDotClass(state: IModelProgressTrust["state"]): string {
    if (state === "returned") return "bg-green-500";
    if (state === "dropped") return "bg-red-600";

    return live.value ? "bg-amber-500" : "bg-gray-400";
}

function trustPillClass(state: IModelProgressTrust["state"]): string {
    if (state === "returned") return "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200";
    if (state === "dropped") return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200";

    return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
}

function trustPillText(trust: IModelProgressTrust): string {
    if (trust.state === "dropped") return trust.lastRound != null ? `out since r${trust.lastRound}` : "dropped";
    if (trust.state === "returned") return live.value ? "returned · waiting" : "returned";

    return live.value && progress.value?.currentRound != null
        ? `gating round ${progress.value.currentRound}`
        : "no result";
}

function trustSegmentClass(trust: IModelProgressTrust, round: number): string {
    if (trust.state === "dropped") {
        return trust.lastRound != null && round <= trust.lastRound
            ? "bg-gray-400 dark:bg-gray-600"
            : "bg-gray-200 dark:bg-dark-raised";
    }
    if (trust.lastRound != null && round <= trust.lastRound) return "bg-primary-500";
    if (trust.state === "training" && round === progress.value?.currentRound && live.value) {
        return "bg-amber-500 ring-[3px] ring-amber-500/25";
    }

    return "bg-gray-200 dark:bg-dark-raised";
}
</script>
