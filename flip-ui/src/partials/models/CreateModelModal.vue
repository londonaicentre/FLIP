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
    <TransitionRoot as="template" :show="open">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-hidden" :unmount="true" @close="closeModal">
            <input class="hidden">
            <div class="absolute inset-0 overflow-hidden">
                <TransitionChild
                    as="template"
                    enter="ease-in-out duration-500"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-in-out duration-500"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>

                <Form
                    class="fixed inset-y-0 right-0 flex max-w-full pl-10"
                    :validation-schema="schema"
                    @submit="submitForm"
                >
                    <TransitionChild
                        as="template"
                        enter="transform transition ease-in-out duration-500 sm:duration-700"
                        enter-from="translate-x-full"
                        enter-to="translate-x-0"
                        leave="transform transition ease-in-out duration-500 sm:duration-700"
                        leave-from="translate-x-0"
                        leave-to="translate-x-full"
                    >
                        <div class="w-screen max-w-4xl">
                            <DialogPanel class="flex flex-col h-full bg-white dark:bg-gray-800 divide-y divide-gray-100 dark:divide-gray-700 shadow-xl dark:ring-1 dark:ring-white/20">
                                <div class="p-4 bg-primary-500 dark:bg-gray-900 sm:px-6">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="text-xl font-bold font-heading text-primary-100 dark:text-gray-300">
                                            Create Model
                                        </DialogTitle>
                                        <div class="flex items-start ml-3 h-7 text-primary-300 dark:text-gray-400">
                                            <button
                                                type="button"
                                                class="cursor-pointer hover:text-primary-100 transition focus:outline-none focus:ring-1 rounded focus:ring-primary-400"
                                                tabindex="0"
                                                @click="closeModal"
                                            >
                                                <span class="sr-only">Close</span>
                                                <icon-mdi-close />
                                            </button>
                                        </div>
                                    </div>
                                    <div class="mt-1">
                                        <p class="text-sm text-primary-200 dark:text-gray-500">
                                            Please confirm the details of your model.
                                        </p>
                                    </div>
                                </div>
                                <div class="flex flex-col flex-1 min-h-0 pb-6 overflow-y-auto">
                                    <div class="relative flex-1 px-4 mt-6 sm:px-6">
                                        <div class="w-full space-y-4">
                                            <AiInput
                                                name="name"
                                                type="text"
                                                label="Model Name"
                                                data-test="model-name"
                                                hint="Used to identify this model throughout the system."
                                            />
                                            <AiTextArea
                                                name="description"
                                                type="text"
                                                label="Model Description"
                                                data-test="model-description"
                                                placeholder="Optional description"
                                                hint="A small description of what your model is trying to achieve."
                                            />
                                        </div>
                                    </div>
                                </div>
                                <div class="flex justify-end flex-shrink-0 p-4 space-x-4 bg-gray-50 dark:bg-gray-900">
                                    <AiButton :data-test="'close-create-project-btn'" @click="closeModal">
                                        Cancel
                                    </AiButton>
                                    <AiButton
                                        primary
                                        class="ml-2"
                                        :loading="formSubmitting"
                                        :disabled="formSubmitting"
                                        :data-test="'create-model-btn'"
                                        type="submit"
                                    >
                                        Create Model
                                    </AiButton>
                                </div>
                            </DialogPanel>
                        </div>
                    </TransitionChild>
                </Form>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { ref } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import AiTextArea from "@/components/AiTextArea/AiTextArea.vue";
import { routeChange } from "@/router";
import { createModel,IModelCreate } from "@/services/model-service";
import { useModalsStore } from "@/store/modals";
import { modelSchema } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";

interface ICreateModelModalProps {
    open: boolean;
}

defineProps<ICreateModelModalProps>();

const modalStore = useModalsStore();

const schema = modelSchema;
const formSubmitting = ref(false);
const route = useRoute();

const closeModal = () => {
    modalStore.toggleCreateModel();
};

const submitForm = async (v: unknown) => {
    if(formSubmitting.value) {
        return;
    }

    formSubmitting.value = true;


    try {
        const values = v as IModelCreate;
        const projectId = route.params["projectId"].toString();

        const { id: modelId } = await createModel("/model", {
            ...values,
            projectId: projectId
        });

        modalStore.toggleCreateModel();

        Snackbar.show({
            type: "success",
            title: "Success",
            text: "Model created successfully"
        });

        routeChange.viewModel(projectId, modelId);
    } catch (e) {
        modalStore.toggleCreateModel();

        Snackbar.show({
            type: "error",
            text: "There was a problem creating this model.",
            title: "Error"
        });
    }

    formSubmitting.value = false;
};
</script>
