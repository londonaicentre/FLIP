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

<!-- The drafting card once a draft is in the editor (FLIP#1556): one line naming the description, with
     the draft's assumptions and concepts folded under it. -->
<template>
    <section
        data-test="draft-summary"
        aria-label="Drafted SQL"
        class="px-4 py-2.5 space-y-2 border rounded-xl bg-primary-100 border-primary-200 dark:bg-dark-raised dark:border-dark-border"
    >
        <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span class="text-xs font-semibold tracking-wider uppercase font-mono text-primary-600 dark:text-primary-200">
                Drafted from
            </span>
            <q data-test="draft-summary-description" :title="description" class="flex-1 min-w-0 text-sm text-gray-800 dark:text-gray-100 truncate">
                {{ description }}
            </q>
            <button
                type="button"
                data-test="draft-redraft"
                class="text-sm font-semibold underline underline-offset-2 text-primary-600 hover:text-primary-800
                dark:text-primary-200 dark:hover:text-primary-100 rounded
                focus:outline-none focus:ring-2 focus:ring-primary-500"
                @click="emit('redraft')"
            >
                Redraft
            </button>
            <button
                type="button"
                data-test="draft-assistant-close"
                aria-label="Close the drafting assistant"
                class="self-center p-1 text-gray-400 dark:text-gray-300 rounded-md hover:text-gray-600
                dark:hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-primary-500"
                @click="emit('close')"
            >
                <icon-mdi-close class="w-4 h-4" aria-hidden="true" />
            </button>
        </div>

        <details data-test="draft-assumptions" class="text-sm text-gray-700 dark:text-gray-300">
            <summary
                data-test="draft-assumptions-title"
                class="font-semibold cursor-pointer text-primary-600 dark:text-primary-200
                focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 rounded"
            >
                {{ title }}
            </summary>
            <div class="mt-2 space-y-2">
                <p
                    v-if="edited"
                    data-test="draft-assumptions-stale"
                    class="px-3 py-2 text-xs rounded-lg bg-amber-50 border border-amber-200 dark:bg-dark-canvas dark:border-dark-border"
                >
                    You have edited the SQL since the draft, so some assumptions may no longer apply.
                </p>
                <ul class="pl-4 space-y-1.5 list-disc">
                    <li v-for="assumption in draft.assumptions" :key="assumption">
                        {{ assumption }}
                    </li>
                </ul>
                <ul aria-label="Concepts used" class="flex flex-wrap gap-1.5">
                    <li
                        v-for="concept in draft.conceptsUsed"
                        :key="concept"
                        class="px-2 py-0.5 text-xs font-mono bg-white border rounded-full text-primary-600
                        border-primary-200 dark:bg-dark-canvas dark:text-primary-200 dark:border-dark-border"
                    >
                        {{ concept }}
                    </li>
                </ul>
            </div>
        </details>
    </section>
</template>

<script lang="ts" setup>
import { computed } from "vue";

import type { ICohortDraft } from "@/services/cohort-draft/types";

const props = defineProps<{
    draft: ICohortDraft;
    /** What the researcher typed, not the drafter's restatement of it. */
    description: string;
    /** The editor no longer holds the draft's SQL. */
    edited: boolean;
}>();

const emit = defineEmits<{ redraft: []; close: [] }>();

const title = computed(() => {
    const n = props.draft.assumptions.length;
    const noun = n === 1 ? "assumption" : "assumptions";

    return `${n} ${noun} behind this draft${props.edited ? " (may be out of date)" : ""}`;
});
</script>
