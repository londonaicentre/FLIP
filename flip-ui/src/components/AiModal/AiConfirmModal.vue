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
        <Dialog
            as="div"
            :open="dialog"
            class="fixed inset-0 z-10 overflow-y-auto"
            @close.capture="close"
        >
            <div class="flex items-end justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0">
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

                <!-- This element is to trick the browser into centering the modal contents. -->
                <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                    enter-to="opacity-100 translate-y-0 sm:scale-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100 translate-y-0 sm:scale-100"
                    leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                >
                    <div
                        data-test="confirm-modal"
                        class="inline-block overflow-hidden text-left align-bottom transition-all transform bg-white rounded-lg shadow-xl dark:bg-gray-800 dark:ring-white/20 dark:ring-1 sm:my-8 sm:align-middle sm:max-w-lg sm:w-full"
                    >
                        <Form
                            v-slot="{meta}"
                            :validation-schema="schema"
                            @submit="continueAction"
                        >
                            <div class="px-4 pt-5 pb-4 bg-white dark:bg-gray-800 sm:p-6 sm:pb-4">
                                <div class="sm:flex sm:items-start">
                                    <div class="w-full text-left">
                                        <DialogTitle as="h3" class="mr-6 text-lg font-bold dark:text-gray-300">
                                            {{ title }}
                                        </DialogTitle>
                                        <div class="mt-2">
                                            <span class="font-normal leading-5 text-gray-700 dark:text-gray-400" v-html="confirmationText" />
                                        </div>
                                        <AiInput
                                            v-if="typingConfirmation"
                                            class="mt-2"
                                            type="text"
                                            name="confirmation"
                                            :placeholder="placeholder"
                                            data-test="confirmation-input"
                                        />
                                    </div>
                                </div>
                            </div>
                            <div class="flex justify-end px-4 py-3 bg-gray-100 dark:bg-gray-900 sm:px-6">
                                <AiButton
                                    :tabindex="0"
                                    class="w-auto"
                                    data-test="close-modal-btn"
                                    :disabled="submitting"
                                    @click="close"
                                >
                                    {{ closeButtonText }}
                                </AiButton>
                                <AiButton
                                    v-if="!!continueAction"
                                    primary
                                    data-test="confirm-modal-btn"
                                    class="w-auto mb-2 ml-2 sm:mb-0"
                                    :disabled="props.typingConfirmation !== '' && !meta.valid || submitting"
                                    :loading="submitting"
                                    @click.once="continueAction"
                                >
                                    {{ continueButtonText }}
                                </AiButton>
                            </div>
                            <div class="absolute top-0 right-0 block pt-4 pr-4">
                                <button
                                    type="button"
                                    class="p-1 text-gray-400 bg-white rounded-md dark:bg-gray-700 hover:text-gray-500 dark:hover:text-gray-300 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
                                    tabindex="0"
                                    :disabled="submitting"
                                    @click="close"
                                >
                                    <span class="sr-only">Close</span>
                                    <icon-mdi-close />
                                </button>
                            </div>
                        </Form>
                    </div>
                </TransitionChild>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { object, string } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiInput from "@/components/AiInput/AiInput.vue";

interface IAiConfirmModalProps {
    dialog: boolean;
    submitting?: boolean;
    continueAction: () => void;
    confirmationText?: string;
    placeholder?: string;
    title?: string;
    closeButtonText?: string;
    continueButtonText?: string;
    typingConfirmation?: string;
}

const props = withDefaults(
    defineProps<IAiConfirmModalProps>(),
    {
        continue: false,
        submitting: false,
        confirmationText: "Are you sure you want to continue?",
        placeholder: "",
        title: "Are you sure?",
        closeButtonText: "Close",
        continueButtonText: "Continue",
        typingConfirmation: ""
    }
);

const schema = object().shape({
    confirmation: string()
        .test("confirmation-match", "", function () {
            return props.typingConfirmation === "" ||
                this.parent.confirmation.toUpperCase() === props.typingConfirmation.toUpperCase();
        })
});

const emit = defineEmits(["closeModal"]);

const close = () => {
    emit("closeModal", false);
};

</script>
