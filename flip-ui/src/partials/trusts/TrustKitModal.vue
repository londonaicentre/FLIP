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
    <TransitionRoot as="template" :show="!!kit">
        <Dialog as="div" class="fixed inset-0 z-10">
            <div class="flex items-center justify-center h-screen min-h-screen p-4 text-center sm:block sm:p-0">
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
                        <div class="inline-flex flex-col w-full transition-all transform bg-white rounded-lg shadow-xl dark:bg-dark-surface">
                            <DialogTitle
                                as="h3"
                                class="px-8 py-4 text-lg font-bold leading-6 text-left text-gray-700 dark:text-gray-300"
                            >
                                {{ trustLabel }} kit: save the passphrase now
                            </DialogTitle>
                            <div class="px-8 py-4 space-y-4 text-sm font-normal leading-5 dark:text-gray-300">
                                <div
                                    class="p-3 border border-red-200 rounded-md bg-red-50 dark:border-red-500/40 dark:bg-red-900/20"
                                    data-test="kit-warning"
                                >
                                    <p class="text-sm font-bold text-red-800 dark:text-red-200">
                                        You won't see this again.
                                    </p>
                                    <p class="text-sm text-red-700 dark:text-red-300 mt-1">
                                        FLIP does not keep the passphrase. Without it, the downloaded file cannot be
                                        opened.
                                    </p>
                                </div>

                                <div>
                                    <div class="flex items-center justify-between mb-1">
                                        <span class="text-xs font-bold text-gray-500 dark:text-gray-300">
                                            Downloaded file
                                        </span>
                                        <button
                                            type="button"
                                            class="text-xs font-semibold text-primary-600 hover:underline"
                                            data-test="kit-save-again-btn"
                                            @click="kit && saveKitFile(kit)"
                                        >
                                            Download again
                                        </button>
                                    </div>
                                    <div
                                        class="font-mono text-sm bg-gray-50 dark:bg-dark-canvas p-2 rounded break-all"
                                        data-test="kit-file-name"
                                    >
                                        {{ kit?.file_name }}
                                    </div>
                                </div>

                                <div>
                                    <div class="flex items-center justify-between mb-1">
                                        <span class="text-xs font-bold text-gray-500 dark:text-gray-300">
                                            Passphrase
                                        </span>
                                        <button
                                            type="button"
                                            class="text-xs font-semibold text-primary-600 hover:underline"
                                            data-test="copy-passphrase-btn"
                                            @click="copyPassphrase"
                                        >
                                            Copy
                                        </button>
                                    </div>
                                    <div
                                        class="font-mono text-sm bg-gray-50 dark:bg-dark-canvas p-2 rounded break-all"
                                        data-test="kit-passphrase"
                                    >
                                        {{ kit?.passphrase }}
                                    </div>
                                </div>

                                <div>
                                    <div class="text-xs font-bold text-gray-500 dark:text-gray-300 mb-1">
                                        On the trust host
                                    </div>
                                    <div
                                        class="font-mono text-xs bg-gray-50 dark:bg-dark-canvas p-2 rounded break-all"
                                        data-test="kit-install-command"
                                    >
                                        make -C trust install-kit FILE={{ kit?.file_name }}
                                    </div>
                                    <p class="mt-1 text-sm text-gray-500 dark:text-gray-300">
                                        Copy the file to the trust host first. The command asks for the passphrase.
                                    </p>
                                    <p
                                        v-if="kit?.kind === 'full'"
                                        class="mt-1 text-sm text-gray-500 dark:text-gray-300"
                                        data-test="kit-public-ip-note"
                                    >
                                        A new site must also send its public IP to the FLIP hub team, so the FL
                                        endpoint admits it.
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
                                        I have saved the passphrase
                                    </span>
                                </label>
                            </div>

                            <div class="px-4 py-3 bg-gray-100 rounded-b-lg dark:bg-dark-canvas sm:px-6 sm:flex sm:flex-row-reverse sm:flex-shrink-0">
                                <AiButton
                                    data-test="kit-done-btn"
                                    primary
                                    class="w-full sm:w-auto"
                                    :disabled="!savedConfirmed"
                                    @click="emit('closeModal')"
                                >
                                    Close
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
import { ITrustKitDownload } from "@/services/trust-service";
import { saveKitFile } from "@/utils/issued-trust-kit";
import { Snackbar } from "@/utils/snackbar";

// The dialog is open while `kit` is set. The host holds the kit and nulls it on close.
const props = defineProps<{
    kit: ITrustKitDownload | null;
    trustLabel: string;
}>();

// The Dialog has no close handler: Esc or a click outside must not discard a passphrase that is shown once.
const emit = defineEmits(["closeModal"]);
const savedConfirmed = ref(false);

// Each kit has its own passphrase, so each needs its own tick.
watch(
    () => props.kit,
    () => {
        savedConfirmed.value = false;
    }
);

const copyPassphrase = async () => {
    try {
        await navigator.clipboard.writeText(props.kit?.passphrase ?? "");
        Snackbar.success({
            title: "Copied",
            text: "Passphrase copied to clipboard."
        });
    } catch {
        Snackbar.error({
            title: "Copy failed",
            text: "Your browser blocked clipboard access. Select the text and copy manually."
        });
    }
};
</script>
