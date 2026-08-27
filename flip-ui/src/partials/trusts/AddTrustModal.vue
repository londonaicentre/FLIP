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
                    <div class="inline-flex flex-col w-full max-w-lg max-h-screen p-4 text-left align-middle rounded-lg">
                        <div class="inline-flex flex-col w-full transition-all transform bg-white rounded-lg shadow-xl dark:bg-dark-surface">
                            <div class="flex items-center justify-between px-8 py-4">
                                <DialogTitle
                                    as="h3"
                                    class="text-lg font-bold leading-6 text-left text-gray-700 dark:text-gray-300"
                                >
                                    Add Trust
                                </DialogTitle>
                                <button
                                    type="button"
                                    data-test="add-trust-close-x-btn"
                                    class="text-gray-400 dark:text-gray-300 hover:text-gray-600 dark:hover:text-gray-100
                                    transition cursor-pointer rounded focus:outline-none focus:ring-1
                                    focus:ring-primary-400"
                                    @click="close"
                                >
                                    <span class="sr-only">Close</span>
                                    <icon-mdi-close class="w-5 h-5" />
                                </button>
                            </div>
                            <div class="px-8 py-4 space-y-4 text-sm font-normal leading-5 dark:text-gray-300">
                                <p>
                                    Registers a trust with the Central Hub. An API key and an internal service key
                                    will be generated and shown <strong>once</strong> on the next screen.
                                </p>
                                <AiInput
                                    class="mt-2"
                                    data-test="trust-name-field"
                                    type="text"
                                    name="name"
                                    label="Trust name"
                                    hint="Full display name shown across the platform. Any characters allowed."
                                    placeholder="e.g. Guy's & St Thomas' NHS Foundation Trust"
                                    :required="true"
                                />
                                <AiInput
                                    data-test="trust-code-field"
                                    type="text"
                                    name="code"
                                    label="Short code"
                                    hint="Short, stable identifier shown in tables and dashboards. Letters, digits, underscore, hyphen."
                                    placeholder="e.g. GSTT"
                                    :required="true"
                                />
                                <AiInput
                                    data-test="trust-region-field"
                                    type="text"
                                    name="region"
                                    label="Region"
                                    hint="NHS region or geography."
                                    placeholder="e.g. London"
                                />
                            </div>
                            <div class="flex flex-row-reverse px-4 py-3 bg-gray-100 rounded-b-lg dark:bg-dark-canvas sm:px-6 shrink-0">
                                <AiButton
                                    data-test="confirm-create-trust-btn"
                                    primary
                                    class="ml-2"
                                    :loading="isSubmitting"
                                    @click="submitAction"
                                >
                                    Create Trust
                                </AiButton>
                                <AiButton
                                    data-test="close-add-trust-modal-btn"
                                    @click="close"
                                >
                                    Cancel
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
import { useField, useForm } from "vee-validate";
import { ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { addTrustSchema } from "@/partials/trusts/addTrustSchema";
import { createAdminTrust, ICreatedTrust } from "@/services/admin-trusts-service";
import { useErrorStore } from "@/store/error";
import { extractErrorDetail } from "@/utils/api-errors";
import { Snackbar } from "@/utils/snackbar";

interface IAddTrustModalProps {
    dialog?: boolean;
}

withDefaults(defineProps<IAddTrustModalProps>(), { dialog: false });

const emit = defineEmits<{
    (e: "closeModal"): void;
    (e: "onSuccess", created: ICreatedTrust): void;
}>();

const errorStore = useErrorStore();
const isSubmitting = ref(false);

const { resetForm, validate } = useForm<{ name: string; code: string; region: string }>(
    { validationSchema: addTrustSchema }
);
const name = useField<string>("name");
const code = useField<string>("code");
const region = useField<string>("region");

const close = () => {
    emit("closeModal");
    resetForm();
};

const submitAction = async () => {
    isSubmitting.value = true;
    try {
        const { valid } = await validate();
        if (!valid) return;

        const created = await createAdminTrust({
            name: name.value.value.trim(),
            code: code.value.value.trim(),
            region: region.value.value?.trim() || undefined
        });
        Snackbar.success({
            title: "Trust created",
            text: `${created.name} has been registered. Save the keys on the next screen.`
        });
        emit("onSuccess", created);
        resetForm();
    } catch (e) {
        Snackbar.error({
            title: "Could not create trust",
            text: extractErrorDetail(e, "There was an error creating the trust. Please try again.")
        });
        errorStore.setError();
        // The dialog overlay would sit on top of the snackbar — close it so the
        // error is actually readable.
        close();
    } finally {
        isSubmitting.value = false;
    }
};
</script>
