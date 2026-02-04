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
        <HeadlessDialog as="div" class="fixed inset-0 z-10 overflow-y-auto" @close.capture="close">
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
                    <div data-test="confirm-modal" class="inline-block overflow-hidden text-left align-bottom transition-all transform bg-white rounded-lg shadow-xl sm:my-8 sm:align-middle sm:max-w-lg sm:w-full">
                        <div class="pt-5 pb-4 bg-white sm:pb-4">
                            <div class="sm:flex sm:items-start">
                                <div class="w-full mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left">
                                    <DialogTitle as="h3" class="px-4 text-lg font-bold leading-6 text-gray-700">
                                        {{ title }}
                                    </DialogTitle>
                                    <div class="mt-2">
                                        <div class="overflow-y-auto text-sm tw-leading-5 tw-font-normal max-h-[500px] px-4">
                                            <slot />
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="px-4 py-3 bg-gray-100 sm:px-6 sm:flex sm:flex-row-reverse">
                            <ai-button
                                class="w-full sm:w-auto"
                                data-test="close-modal-btn"
                                @click="close"
                            >
                                Close
                            </ai-button>
                        </div>
                    </div>
                </TransitionChild>
            </div>
        </HeadlessDialog>
    </TransitionRoot>
</template>

<script lang="ts">
import { Dialog as HeadlessDialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { defineComponent } from "vue";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";

export default defineComponent({
    components: {
        HeadlessDialog,
        DialogTitle,
        TransitionChild,
        TransitionRoot,
        AiDialogOverlay
    },
    props: {
        dialog: {
            type: Boolean,
            required: true,
            default: false
        },
        title: {
            type: String,
            required: true
        }
    },
    emits: ["closeModal"],
    methods: {
        close() {
            this.$emit("closeModal", false);
        }
    }
});
</script>
