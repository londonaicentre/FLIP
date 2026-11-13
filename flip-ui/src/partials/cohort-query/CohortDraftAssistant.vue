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

<!-- Optional SQL drafting assistant (FLIP#1556 UI mock). Lives inside the cohort <Form> and writes
     only the `query` field; every control is type="button", so Run stays the only submit. -->
<template>
    <div>
        <button
            v-if="!open"
            type="button"
            data-test="draft-assistant-open"
            :aria-expanded="false"
            class="inline-flex items-center gap-1.5 text-sm font-semibold text-primary-600 hover:text-primary-800
            dark:text-primary-200 dark:hover:text-primary-100 focus:outline-none focus:underline"
            @click="open = true"
        >
            <icon-ph-magic-wand class="w-4 h-4" aria-hidden="true" />
            Draft SQL from a description
        </button>

        <section
            v-else
            data-test="draft-assistant"
            aria-labelledby="draft-assistant-title"
            class="p-4 space-y-3 bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
        >
            <div class="flex items-center justify-between">
                <h2
                    id="draft-assistant-title"
                    class="flex items-center gap-1.5 text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-300"
                >
                    <icon-ph-magic-wand class="w-4 h-4" aria-hidden="true" />
                    Draft SQL from a description
                </h2>
                <button
                    type="button"
                    data-test="draft-assistant-close"
                    aria-label="Close the drafting assistant"
                    class="p-1 text-gray-400 dark:text-gray-300 rounded-md hover:text-gray-600 dark:hover:text-gray-200
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                    @click="close"
                >
                    <icon-mdi-close class="w-4 h-4" aria-hidden="true" />
                </button>
            </div>

            <div>
                <label for="draft-description" class="text-sm font-bold text-gray-700 dark:text-gray-300">
                    Describe the cohort
                </label>
                <!-- A plain v-model textarea, not a vee-validate field: a registered field would be
                     sent to /step/cohort with the query when the form submits. -->
                <textarea
                    id="draft-description"
                    v-model="description"
                    data-test="draft-description"
                    rows="3"
                    maxlength="2000"
                    aria-describedby="draft-identifiers-warning"
                    :placeholder="examples[0]"
                    class="block w-full mt-1 text-sm text-gray-700 dark:text-gray-300 border-gray-300 rounded-md
                    shadow-sm resize-y dark:bg-dark-raised dark:border-dark-border placeholder:text-gray-400
                    focus:border-primary-500 focus:ring-primary-500
                    dark:focus:border-primary-400 dark:focus:ring-primary-400"
                />
                <p
                    id="draft-identifiers-warning"
                    class="flex items-start gap-1.5 mt-1.5 text-xs text-gray-600 dark:text-gray-300"
                >
                    <icon-ph-warning-circle class="w-4 h-4 shrink-0 text-deeporange-900 dark:text-status-staged" aria-hidden="true" />
                    <span>
                        <strong>Do not enter patient identifiers</strong> — no names, NHS numbers, dates of birth or
                        addresses. Describe the criteria only.
                    </span>
                </p>
            </div>

            <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-xs text-gray-500 dark:text-gray-300">Try:</span>
                <button
                    v-for="example in examples"
                    :key="example"
                    type="button"
                    data-test="draft-example"
                    class="px-2 py-0.5 text-xs text-gray-700 dark:text-gray-300 bg-gray-100 rounded-full
                    hover:bg-primary-100 dark:bg-dark-raised dark:hover:bg-dark-surface
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                    @click="description = example"
                >
                    {{ example }}
                </button>
            </div>

            <div class="flex flex-wrap items-center gap-3">
                <AiButton
                    type="button"
                    primary
                    data-test="draft-generate"
                    :disabled="!canGenerate"
                    @click="draft.generate(description)"
                >
                    <icon-ph-magic-wand class="w-4 h-4 mr-1.5" aria-hidden="true" />
                    Generate SQL
                </AiButton>
                <DraftUsageIndicator v-if="draft.usage.value" :usage="draft.usage.value" />
                <template v-if="draft.phase.value === 'drafting'">
                    <span
                        data-test="draft-progress"
                        class="inline-flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300"
                    >
                        <icon-ph-circle-notch class="w-4 h-4 animate-spin" aria-hidden="true" />
                        Drafting SQL…
                    </span>
                    <AiButton
                        type="button"
                        small
                        text-secondary
                        data-test="draft-cancel"
                        @click="draft.cancel()"
                    >
                        Cancel
                    </AiButton>
                </template>
                <span class="text-xs text-gray-500 dark:text-gray-300 sm:ml-auto">
                    {{ shapeNote }}
                </span>
            </div>

            <div aria-live="polite" class="empty:hidden">
                <p
                    v-if="draft.exhausted.value"
                    data-test="draft-exhausted"
                    class="px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg dark:bg-dark-raised dark:text-gray-300"
                >
                    You have used all {{ draft.usage.value?.limit }} drafts this month. Drafting resets
                    {{ resetDate }} (00:00 UTC). You can still write and run SQL yourself.
                </p>

                <div
                    v-else-if="draft.phase.value === 'clarifying' && draft.clarification.value"
                    data-test="draft-clarification"
                    class="p-3 space-y-2 rounded-lg bg-primary-50 dark:bg-dark-raised"
                >
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-100">
                        {{ draft.clarification.value.question }}
                    </p>
                    <div class="flex flex-wrap items-center gap-2">
                        <AiButton
                            v-for="option in draft.clarification.value.options"
                            :key="option"
                            type="button"
                            small
                            data-test="draft-clarification-option"
                            @click="draft.answer(description, option)"
                        >
                            {{ option }}
                        </AiButton>
                        <input
                            v-model="reply"
                            type="text"
                            data-test="draft-clarification-reply"
                            aria-label="Your answer"
                            placeholder="Or type an answer"
                            class="flex-1 min-w-[10rem] py-1.5 text-sm border-gray-300 rounded-md dark:bg-dark-canvas
                            dark:border-dark-border dark:text-gray-300 focus:border-primary-500 focus:ring-primary-500"
                            @keydown.enter.prevent="submitReply"
                        >
                        <AiButton type="button" small data-test="draft-clarification-send" @click="submitReply">
                            Answer
                        </AiButton>
                    </div>
                </div>

                <div
                    v-else-if="draft.phase.value === 'declined'"
                    data-test="draft-declined"
                    class="flex gap-2 p-3 text-sm text-gray-700 rounded-lg bg-amber-50 dark:bg-dark-raised dark:text-gray-300"
                >
                    <icon-ph-prohibit class="w-5 h-5 shrink-0 text-deeporange-900 dark:text-status-staged" aria-hidden="true" />
                    <div>
                        <p class="font-semibold">
                            No SQL was drafted.
                        </p>
                        <p class="mt-1">
                            {{ draft.message.value }}
                        </p>
                    </div>
                </div>

                <div
                    v-else-if="draft.phase.value === 'failed'"
                    data-test="draft-failed"
                    class="flex items-start gap-2 p-3 text-sm text-red-700 rounded-lg bg-red-50 dark:bg-dark-raised dark:text-status-unstaged"
                >
                    <icon-ph-warning class="w-5 h-5 shrink-0" aria-hidden="true" />
                    <div class="flex-1">
                        <p class="font-semibold">
                            The draft could not be generated. Your SQL is unchanged.
                        </p>
                        <p class="mt-1">
                            {{ draft.message.value }}
                        </p>
                    </div>
                    <AiButton type="button" small data-test="draft-retry" @click="draft.generate(description)">
                        Try again
                    </AiButton>
                </div>

                <div
                    v-else-if="draft.phase.value === 'kept-edits'"
                    data-test="draft-kept-edits"
                    class="flex flex-wrap items-center gap-2 p-3 text-sm text-gray-700 rounded-lg bg-amber-50 dark:bg-dark-raised dark:text-gray-300"
                >
                    <p class="flex-1">
                        You edited the SQL while the draft was being prepared, so your edits were kept and the draft
                        was not inserted.
                    </p>
                    <AiButton type="button" small data-test="draft-use-held" @click="draft.acceptHeldDraft()">
                        Replace my edits with the draft
                    </AiButton>
                </div>

                <p
                    v-else-if="draft.phase.value === 'drafted'"
                    data-test="draft-inserted"
                    class="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300"
                >
                    <icon-ph-check-circle class="w-4 h-4 text-green-600 dark:text-status-approved" aria-hidden="true" />
                    Draft placed in the editor. Review and edit it, then run it on all trusts.
                </p>
            </div>
        </section>

        <AiConfirmModal
            :dialog="draft.phase.value === 'confirming'"
            :continue-action="draft.confirmReplace"
            title="Replace the SQL in the editor?"
            confirmation-text="Generating a draft will replace the SQL currently in the editor. Copy it first if you want to keep it."
            close-button-text="Keep my SQL"
            continue-button-text="Replace and generate"
            @close-modal="draft.cancelReplace()"
        />
    </div>
</template>

<script lang="ts" setup>
import { useFieldValue, useSetFieldValue } from "vee-validate";
import { computed, ref, watch } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { formatDraftReset, useCohortDraft } from "@/composables/useCohortDraft";
import { getPageDrafter } from "@/services/cohort-draft/fake-drafter";
import type { ICohortDraft, ICohortDrafter } from "@/services/cohort-draft/types";

import DraftUsageIndicator from "./DraftUsageIndicator.vue";

const props = defineProps<{
    hasImaging: boolean;
    /** Injected in tests; the page uses the session's fake drafter. */
    drafter?: ICohortDrafter;
}>();

const emit = defineEmits<{ notes: [draft: ICohortDraft | null] }>();

const open = ref(false);
const description = ref("");
const reply = ref("");

// The cohort form's `query` field, i.e. the existing editor.
const query = useFieldValue<string | undefined>("query");
const setQuery = useSetFieldValue<string>("query");

const draft = useCohortDraft(
    props.drafter ?? getPageDrafter(),
    {
        get: () => query.value ?? "",
        set: (sql) => setQuery(sql)
    },
    () => props.hasImaging
);

const canGenerate = computed(() =>
    !!description.value.trim() && !draft.exhausted.value && draft.phase.value !== "drafting");

const resetDate = computed(() => draft.usage.value ? formatDraftReset(draft.usage.value.resetsAt) : "");

const examples = computed(() => props.hasImaging
    ? [
        "Chest X-rays of adult patients (18 or over at the time of the image) for a pneumonia classifier.",
        "All CT scans of women aged over 60.",
        "Scans of women aged over 60."
    ]
    : ["Patients with type 2 diabetes, with their age and sex, for a tabular risk model. No imaging."]);

const shapeNote = computed(() => props.hasImaging
    ? "Imaging project: drafts return person_id and accession_id."
    : "Tabular-only project: drafts return person_id.");

const submitReply = () => {
    draft.answer(description.value, reply.value);
    reply.value = "";
};

const close = () => {
    draft.cancel();
    open.value = false;
};

watch(draft.inserted, (d) => emit("notes", d));
</script>
