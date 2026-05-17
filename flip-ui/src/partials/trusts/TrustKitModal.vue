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

<template>
    <TransitionRoot as="template" :show="dialog">
        <Dialog as="div" class="fixed inset-0 z-10" @close.capture="close">
            <div class="flex items-end justify-center h-screen min-h-screen px-4 pt-4 text-center sm:block sm:p-0">
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>
                <span class="inline-block align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                    enter-to="opacity-100 translate-y-0 sm:scale-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100 translate-y-0 sm:scale-100"
                    leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                >
                    <div class="inline-flex flex-col w-full max-w-2xl max-h-screen p-4 text-left align-middle rounded-lg">
                        <div class="inline-flex flex-col w-full transition-all transform bg-white rounded-lg shadow-xl dark:bg-gray-800">
                            <DialogTitle
                                as="h3"
                                class="px-8 py-4 text-lg font-bold leading-6 text-left text-gray-700 dark:text-gray-300"
                            >
                                Trust kit — save these now
                            </DialogTitle>
                            <div class="px-8 py-4 space-y-4 text-sm font-normal leading-5 dark:text-gray-400">
                                <div
                                    class="border-l-4 border-red-500 bg-red-50 dark:bg-red-900/20 p-3"
                                    data-test="kit-warning"
                                >
                                    <p class="text-sm font-bold text-red-800 dark:text-red-200">
                                        You won't see these again.
                                    </p>
                                    <p class="text-xs text-red-700 dark:text-red-300 mt-1">
                                        Once this dialog closes, the plaintext keys are unrecoverable. The hub only
                                        stores their hashes. Save them in a password manager and hand them to the
                                        trust operator over a secure channel (Signal, encrypted email).
                                    </p>
                                </div>

                                <div>
                                    <div class="text-xs font-bold uppercase tracking-widest text-gray-500 mb-1">
                                        Trust name
                                    </div>
                                    <div class="font-mono text-sm bg-gray-50 dark:bg-gray-900 p-2 rounded">
                                        {{ trust?.name ?? "—" }}
                                    </div>
                                </div>

                                <div>
                                    <div class="flex items-center justify-between mb-1">
                                        <span class="text-xs font-bold uppercase tracking-widest text-gray-500">
                                            TRUST_API_KEY
                                        </span>
                                        <button
                                            type="button"
                                            class="text-xs text-primary-600 hover:underline"
                                            data-test="copy-api-key-btn"
                                            @click="copy(trust?.trust_api_key ?? '', 'API key')"
                                        >
                                            Copy
                                        </button>
                                    </div>
                                    <div
                                        class="font-mono text-xs bg-gray-50 dark:bg-gray-900 p-2 rounded break-all"
                                        data-test="api-key-value"
                                    >
                                        {{ trust?.trust_api_key ?? "—" }}
                                    </div>
                                </div>

                                <div>
                                    <div class="flex items-center justify-between mb-1">
                                        <span class="text-xs font-bold uppercase tracking-widest text-gray-500">
                                            TRUST_INTERNAL_SERVICE_KEY
                                        </span>
                                        <button
                                            type="button"
                                            class="text-xs text-primary-600 hover:underline"
                                            data-test="copy-internal-key-btn"
                                            @click="copy(trust?.trust_internal_service_key ?? '', 'Internal service key')"
                                        >
                                            Copy
                                        </button>
                                    </div>
                                    <div
                                        class="font-mono text-xs bg-gray-50 dark:bg-gray-900 p-2 rounded break-all"
                                        data-test="internal-key-value"
                                    >
                                        {{ trust?.trust_internal_service_key ?? "—" }}
                                    </div>
                                </div>

                                <div>
                                    <div class="flex items-center justify-between mb-1">
                                        <span class="text-xs font-bold uppercase tracking-widest text-gray-500">
                                            FL kit slot
                                        </span>
                                        <button
                                            type="button"
                                            class="text-xs text-primary-600 hover:underline"
                                            data-test="copy-fl-kit-slot-btn"
                                            @click="copy(trust?.fl_kit_slot ?? '', 'FL kit slot')"
                                        >
                                            Copy
                                        </button>
                                    </div>
                                    <div
                                        class="font-mono text-xs bg-gray-50 dark:bg-gray-900 p-2 rounded break-all"
                                        data-test="fl-kit-slot-value"
                                    >
                                        {{ trust?.fl_kit_slot ?? "—" }} (slot #{{ trust?.fl_kit_slot_number ?? "—" }})
                                    </div>
                                    <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                        Operator mounts <code>workspace/net-N/services/{{ trust?.fl_kit_slot ?? "—" }}/</code>
                                        from flip-fl-base; their FL clients register with the FL server under this identity
                                        regardless of the friendly name above.
                                    </p>
                                </div>

                                <label class="flex items-center gap-2 mt-4 cursor-pointer">
                                    <input
                                        v-model="savedConfirmed"
                                        type="checkbox"
                                        data-test="kit-saved-checkbox"
                                        class="text-primary-600 rounded"
                                    >
                                    <span class="text-sm text-gray-700 dark:text-gray-300">
                                        I've saved the keys somewhere secure.
                                    </span>
                                </label>
                            </div>

                            <div class="px-4 py-3 bg-gray-100 rounded-b-lg dark:bg-gray-900 sm:px-6 sm:flex sm:flex-row-reverse sm:flex-shrink-0">
                                <AiButton
                                    data-test="kit-done-btn"
                                    primary
                                    class="w-full sm:w-auto"
                                    :disabled="!savedConfirmed"
                                    @click="close"
                                >
                                    Done
                                </AiButton>
                            </div>
                        </div>
                    </div>
                </TransitionChild>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { ref, watch } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import { ICreatedTrust } from "@/services/admin-trusts-service";
import { Snackbar } from "@/utils/snackbar";

interface ITrustKitModalProps {
    dialog: boolean;
    trust: ICreatedTrust | null;
}

const props = withDefaults(defineProps<ITrustKitModalProps>(), {
    dialog: false,
    trust: null
});

const emit = defineEmits(["closeModal"]);
const savedConfirmed = ref(false);

// Reset the saved-confirmed checkbox each time a fresh trust is surfaced.
watch(
    () => props.trust?.id,
    () => {
        savedConfirmed.value = false;
    }
);

const close = () => {
    emit("closeModal");
};

const copy = async (value: string, label: string) => {
    try {
        await navigator.clipboard.writeText(value);
        Snackbar.success({
            title: "Copied",
            text: `${label} copied to clipboard.`
        });
    } catch {
        Snackbar.error({
            title: "Copy failed",
            text: "Your browser blocked clipboard access. Select the text and copy manually."
        });
    }
};
</script>
