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

<!-- Design ref: design_handoff_full/04_lifecycle/lifecycle.jsx — variant
     "B · Horizontal track" (LifecycleB, lines 139-171). Self-contained: the
     "current" stage is derived as the first non-completed step so callers can
     keep passing the same IStep[] they pass to AiSteps elsewhere. -->

<template>
    <div class="shrink-0">
        <div class="px-3 py-0.5">
            <div
                class="relative grid items-start px-2"
                :style="{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }"
                role="list"
                aria-label="Project lifecycle"
            >
                <!-- Track (rail + progress) sits behind the circles; left/right
                     inset by half-stage so it stops at the edges of the row. -->
                <div
                    class="absolute h-[2px] bg-gray-200 dark:bg-dark-raised rounded"
                    :style="trackStyle"
                />
                <div
                    v-if="progressWidthPct > 0"
                    class="absolute h-[2px] bg-primary-500 rounded transition-all"
                    :style="progressStyle"
                />
                <div
                    v-for="(step, idx) in steps"
                    :key="step.id"
                    role="listitem"
                    class="relative z-10 flex flex-col items-center text-center"
                >
                    <div
                        class="grid w-5 h-5 place-items-center rounded-full transition-all mb-1"
                        :class="circleClass(idx)"
                    >
                        <icon-heroicons-outline-x
                            v-if="step.error && !step.completed"
                            class="w-3 h-3 text-white stroke-[2.5]"
                            aria-hidden="true"
                        />
                        <icon-heroicons-outline-minus
                            v-else-if="step.stopped && !step.completed"
                            class="w-3 h-3 text-white stroke-[2.5]"
                            aria-hidden="true"
                        />
                        <icon-heroicons-outline-check
                            v-else-if="step.completed"
                            class="w-3 h-3 text-white stroke-[2.5]"
                            aria-hidden="true"
                        />
                        <span
                            v-else-if="isCurrent(idx)"
                            class="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse"
                            aria-hidden="true"
                        />
                    </div>
                    <div
                        class="font-heading font-semibold text-xs leading-tight"
                        :class="stepTextClass(idx)"
                    >
                        {{ step.name }}
                    </div>
                    <div
                        v-if="stageSubLabel(idx)"
                        class="text-[10px] text-gray-500 dark:text-gray-300 mt-0.5 font-mono"
                    >
                        {{ stageSubLabel(idx) }}
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { IStep } from "@/interfaces/steps";

interface ILifecycleTrackProps {
    steps: IStep[];
}

const props = defineProps<ILifecycleTrackProps>();

const completedCount = computed(() => props.steps.filter(s => s.completed).length);

// Current stage = first non-completed step. Caller doesn't have to set
// `inProgress` explicitly — matches the implicit "next thing to do" the
// existing AiSteps consumers rely on.
const currentIndex = computed(() => props.steps.findIndex(s => !s.completed));

const isCurrent = (idx: number) => idx === currentIndex.value;

const circleClass = (idx: number): string => {
    const step = props.steps[idx];
    if (step.error && !step.completed) return "bg-red-500 border-2 border-red-500";
    if (step.stopped && !step.completed) return "bg-yellow-500 border-2 border-yellow-500";
    if (step.completed) return "bg-primary-500 border-2 border-primary-500";
    if (isCurrent(idx)) {
        return "bg-white dark:bg-dark-canvas border-[2.5px] border-primary-500 shadow-[0_0_0_5px_rgba(97,54,110,0.15)]";
    }

    return "bg-white dark:bg-dark-canvas border-2 border-gray-300 dark:border-dark-border-strong";
};

const stepTextClass = (idx: number): string => {
    const step = props.steps[idx];
    if (step.error && !step.completed) return "text-red-600 dark:text-red-400";
    if (step.stopped && !step.completed) return "text-yellow-700 dark:text-yellow-400";
    if (step.completed || isCurrent(idx)) return "text-gray-900 dark:text-gray-100";

    return "text-gray-500 dark:text-gray-300";
};

const formatStageDate = (iso: string): string => new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric"
});

const stageSubLabel = (idx: number): string => {
    const step = props.steps[idx];
    if (step.error && !step.completed) return "error";
    if (step.stopped && !step.completed) return "stopped";
    if (step.date) return formatStageDate(step.date);
    if (isCurrent(idx)) return "in progress";
    if (step.completed) return "done";

    return "pending";
};

// Track + progress geometry. The track spans the row centred on the circles,
// so it has the same horizontal inset on both sides (≈ one half-cell).
const trackStyle = computed(() => {
    const inset = `${50 / props.steps.length}%`;

    return {
        top: "9px",
        left: inset,
        right: inset
    };
});

const progressWidthPct = computed(() => {
    if (props.steps.length <= 1) return 0;
    // Progress reaches the centre of the rightmost done circle. If only the
    // first circle is done we render 0 width (the circle itself communicates
    // progress); otherwise scale by (done - 1) / (count - 1).
    const done = completedCount.value;

    return done <= 1 ? 0 : ((done - 1) / (props.steps.length - 1)) * 100;
});

const progressStyle = computed(() => {
    const inset = `${50 / props.steps.length}%`;
    const fullWidth = `(100% - 2 * ${inset})`;

    return {
        top: "9px",
        left: inset,
        width: `calc(${fullWidth} * ${progressWidthPct.value / 100})`
    };
});
</script>
