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

<!-- Monthly draft allowance (FLIP#1556): a ring that fills as drafts are used, with the remaining
     count printed inside so the state never rests on colour alone. A button, so it takes focus
     and a tap; the tooltip and the accessible label carry the same sentence. -->
<template>
    <button
        v-tippy="{ content: tooltip, placement: 'top', allowHTML: false }"
        type="button"
        data-test="draft-usage-indicator"
        :aria-label="label"
        class="relative inline-flex items-center justify-center w-8 h-8 rounded-full shrink-0
        focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 dark:ring-offset-dark-canvas"
    >
        <svg viewBox="0 0 36 36" class="w-8 h-8 -rotate-90" aria-hidden="true">
            <circle
                cx="18"
                cy="18"
                :r="RADIUS"
                fill="none"
                stroke-width="4"
                class="stroke-gray-200 dark:stroke-dark-border-strong"
            />
            <circle
                cx="18"
                cy="18"
                :r="RADIUS"
                fill="none"
                stroke-width="4"
                stroke-linecap="round"
                :stroke-dasharray="`${filled} ${CIRCUMFERENCE}`"
                :class="remaining === 0 ? 'stroke-gray-500 dark:stroke-gray-300' : 'stroke-primary-500 dark:stroke-primary-300'"
            />
        </svg>
        <span
            aria-hidden="true"
            class="absolute text-[10px] font-bold tabular-nums text-gray-700 dark:text-gray-200"
        >
            {{ remaining }}
        </span>
    </button>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { directive as vTippy } from "vue-tippy";

import { formatDraftReset } from "@/composables/useCohortDraft";
import type { IDraftUsage } from "@/services/cohort-draft/types";

const props = defineProps<{ usage: IDraftUsage }>();

const RADIUS = 15;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const remaining = computed(() => Math.max(props.usage.limit - props.usage.used, 0));
// A zero allowance reads as full, not NaN.
const filled = computed(() =>
    CIRCUMFERENCE * (props.usage.limit ? Math.min(props.usage.used / props.usage.limit, 1) : 1));

const resetDate = computed(() => formatDraftReset(props.usage.resetsAt));

const label = computed(() =>
    `${remaining.value} of ${props.usage.limit} drafts remaining. Resets ${resetDate.value}.`);
const tooltip = computed(() => `${label.value} (00:00 UTC)`);
</script>
