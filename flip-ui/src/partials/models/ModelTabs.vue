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

<!-- Ghost tab nav for the model page (design 04·A ProjectChrome, TAB-NAV.md):
     Prepare -> Run as flat ghost buttons — no border, no underline; the soft
     paper fill is the whole active indicator. Run is locked until the model is
     dispatched, since there is nothing to watch. -->
<template>
    <nav class="flex items-center gap-1" aria-label="Model stages">
        <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            :data-test="`tab-${tab.id}`"
            :disabled="tab.locked"
            :aria-current="tab.id === modelValue ? 'page' : undefined"
            class="inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-[13px] font-semibold
                   transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500
                   focus-visible:ring-offset-2 dark:focus-visible:ring-offset-dark-canvas"
            :class="tabClass(tab)"
            @click="select(tab)"
        >
            <span
                v-if="tab.id === 'run' && live"
                class="relative flex items-center justify-center w-1.5 h-1.5"
            >
                <span
                    data-test="tab-run-live"
                    class="absolute inline-flex w-full h-full rounded-full opacity-60 animate-ping bg-fuchsia-500"
                />
                <span class="relative inline-flex w-1.5 h-1.5 rounded-full bg-fuchsia-500" />
            </span>

            <icon-ph-check-bold
                v-if="tab.done"
                :data-test="`tab-${tab.id}-done`"
                class="w-3 h-3 text-green-600 dark:text-green-400"
                aria-hidden="true"
            />

            {{ tab.label }}

            <span
                v-if="tab.locked"
                class="font-mono text-[10px] font-normal tracking-[0.04em]"
            >after start</span>
        </button>
    </nav>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { getStatusEnumValue, type ModelStatus, ModelStatusEnum } from "@/services/model-service";

export type ModelTab = "prepare" | "run";

interface Tab {
    id: ModelTab;
    label: string;
    locked: boolean;
    done: boolean;
}

const props = defineProps<{
    status?: ModelStatus | string;
    modelValue: ModelTab;
}>();

const emit = defineEmits<{ "update:modelValue": [tab: ModelTab] }>();

const statusEnum = computed(() => getStatusEnumValue(props.status as ModelStatus));

// PENDING is the only state where nothing has been dispatched, so it is the only
// state where the Run tab has nothing to show.
const pending = computed(() => statusEnum.value === ModelStatusEnum.PENDING);
const finished = computed(() =>
    [
        ModelStatusEnum.ERROR,
        ModelStatusEnum.RESULTS_UPLOADED,
        ModelStatusEnum.RESULTS_UPLOAD_FAILED,
        ModelStatusEnum.STOPPED
    ].includes(statusEnum.value)
);
const live = computed(() => !pending.value && !finished.value);

const tabs = computed<Tab[]>(() => [
    {
        id: "prepare",
        label: "Prepare",
        locked: false,
        // A dispatched model is prepared, whichever tab you happen to be looking at.
        done: !pending.value
    },
    {
        id: "run",
        label: "Run",
        locked: pending.value,
        // Only a delivered run is done. A stopped, errored or upload-failed run
        // has also finished, but a tick would read as success.
        done: statusEnum.value === ModelStatusEnum.RESULTS_UPLOADED
    }
]);

// Active is ink on the soft paper fill; inactive is muted ink over nothing
// (TAB-NAV.md). Dark mode maps ink onto the standard gray ramp and the fill
// onto the raised surface, per the palette rules in tailwind.config.js.
function tabClass(tab: Tab): string {
    if (tab.id === props.modelValue) return "text-ink bg-paper-2 dark:text-gray-100 dark:bg-dark-raised";
    // The dark ink floor is gray-300 (see dark-mode-contrast guard); opacity
    // carries the locked dimming instead of a darker gray.
    if (tab.locked) return "text-ink-3 dark:text-gray-300 opacity-60 cursor-default";

    return "text-ink-3 hover:text-ink dark:text-gray-300 dark:hover:text-gray-200";
}

function select(tab: Tab): void {
    if (tab.locked || tab.id === props.modelValue) return;
    emit("update:modelValue", tab.id);
}
</script>
