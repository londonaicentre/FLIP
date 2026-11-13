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

<!-- Assumptions and concepts of the draft placed in the editor (FLIP#1556), shown beside it. -->
<template>
    <section
        data-test="draft-notes"
        aria-labelledby="draft-notes-title"
        class="overflow-hidden bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
    >
        <div class="flex items-center justify-between px-5 pt-4">
            <h2
                id="draft-notes-title"
                class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-300"
            >
                Draft assumptions
            </h2>
            <button
                type="button"
                data-test="draft-notes-dismiss"
                aria-label="Hide the draft assumptions"
                class="p-1 text-gray-400 dark:text-gray-300 rounded-md hover:text-gray-600 dark:hover:text-gray-200
                focus:outline-none focus:ring-2 focus:ring-primary-500"
                @click="emit('dismiss')"
            >
                <icon-mdi-close class="w-4 h-4" aria-hidden="true" />
            </button>
        </div>
        <div class="px-5 pb-4 overflow-y-auto max-h-80">
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-300">
                Check these before running. Editing the SQL does not update them.
            </p>
            <ul class="mt-2 space-y-1.5 text-sm list-disc pl-4 text-gray-700 dark:text-gray-300">
                <li v-for="assumption in draft.assumptions" :key="assumption">
                    {{ assumption }}
                </li>
            </ul>
            <h3 class="mt-3 text-xs font-semibold text-gray-500 dark:text-gray-300">
                Concepts used
            </h3>
            <ul class="mt-1 space-y-0.5 text-xs font-mono text-gray-600 dark:text-gray-300">
                <li v-for="concept in draft.conceptsUsed" :key="concept">
                    {{ concept }}
                </li>
            </ul>
            <details class="mt-3 text-sm text-gray-700 dark:text-gray-300">
                <summary class="text-xs font-semibold text-gray-500 cursor-pointer dark:text-gray-300">
                    How the draft works
                </summary>
                <p class="mt-1">
                    {{ draft.explanation }}
                </p>
            </details>
        </div>
    </section>
</template>

<script lang="ts" setup>
import type { ICohortDraft } from "@/services/cohort-draft/types";

defineProps<{ draft: ICohortDraft }>();

const emit = defineEmits<{ dismiss: [] }>();
</script>
