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

<template>
    <TransitionRoot as="template" :show="dialog">
        <Dialog as="div" class="fixed inset-0 z-10" :unmount="true" @close.capture="close">
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

                <!-- This element is to trick the browser into centering the modal contents. -->
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
                    <div
                        class="inline-flex flex-col w-full max-h-screen text-left align-middle rounded-lg max-w-screen-2xl"
                        :class="{'!max-w-xl': small}"
                    >
                        <div
                            data-test="confirm-modal"
                            class="inline-flex flex-col w-full overflow-hidden transition-all transform bg-body dark:bg-gray-800 rounded-lg shadow-xl dark:ring-white/20"
                        >
                            <DialogTitle
                                v-if="title"
                                as="h3"
                                class="px-8 pt-8 pb-2 text-xl font-bold leading-6 text-left"
                            >
                                {{ title }}
                            </DialogTitle>
                            <DialogTitle
                                v-if="description"
                                as="h5"
                                class="px-8 pb-2 text-sm font-medium leading-6 text-left text-gray-600"
                            >
                                {{ description }}
                            </DialogTitle>

                            <div class="flex flex-grow overflow-y-auto bg-body dark:bg-gray-800">
                                <div class="flex flex-col items-start w-full">
                                    <div class="w-full text-left">
                                        <div
                                            class="relative w-full overflow-y-auto text-sm font-normal leading-5"
                                        >
                                            <slot />
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div
                                v-if="!basic"
                                class="flex justify-end flex-shrink-0 w-full px-4 py-3 bg-white dark:bg-gray-900 sm:px-6"
                            >
                                <AiButton :tabindex="0" data-test="close-modal-btn" @click="close">
                                    Close
                                </AiButton>
                                <AiButton
                                    v-if="continueAction"
                                    primary
                                    class="w-full ml-2 sm:w-auto"
                                    data-test="submit-modal-btn"
                                    :disabled="disabledContinueButton || loadingContinueButton"
                                    :loading="loadingContinueButton"
                                    @click.once="continueAction"
                                >
                                    {{ continueButtonText }}
                                </AiButton>
                            </div>
                            <div class="absolute block top-2 right-2">
                                <button
                                    type="button"
                                    class="text-gray-400 bg-white dark:bg-gray-700 dark:text-gray-200 p-1 border border-gray-300 rounded-lg dark:focus:ring-primary-400 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500 dark:ring-offset-gray-900"
                                    tabindex="0"
                                    @click="close"
                                >
                                    <span class="sr-only">Close</span>
                                    <icon-mdi-close class="h-5 w-5" />
                                </button>
                            </div>
                        </div>
                    </div>
                </TransitionChild>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script lang="ts" setup>
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";

interface IAiModalProps {
    dialog: boolean;
    title?: string;
    description?: string;
    small?: boolean;
    basic?: boolean;
    closeButtonText?: string;
    continueButtonText?: string;
    continueAction?: () => void;
    disabledContinueButton?: boolean;
    loadingContinueButton?: boolean;
}

withDefaults(
    defineProps<IAiModalProps>(),
    {
        title: undefined,
        description: undefined,
        closeButtonText: "Close",
        continueButtonText: "Continue",
        continueAction: undefined,
        disabledContinueButton: false,
        loadingContinueButton: false
    }
);

const emit = defineEmits(["closeModal"]);

const close = () => {
    emit("closeModal", false);
};
</script>
