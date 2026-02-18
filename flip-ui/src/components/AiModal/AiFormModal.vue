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
                    <div class="inline-flex flex-col w-full max-h-screen p-4 text-left align-middle rounded-lg xl:!max-w-xl">
                        <Form
                            data-test="form-modal"
                            class="inline-flex flex-col w-full overflow-hidden transition-all transform bg-white rounded-lg shadow-xl"
                            :validation-schema="schema"
                            @submit="submit"
                        >
                            <DialogTitle v-if="title" as="h3" class="px-8 pt-8 pb-2 text-xl font-bold leading-6 text-left">
                                {{ title }}
                            </DialogTitle>
                            <DialogTitle v-if="description" as="h5" class="px-8 pb-2 text-sm font-medium leading-6 text-left text-gray-600">
                                {{ description }}
                            </DialogTitle>

                            <div class="flex flex-grow overflow-y-auto bg-white">
                                <div class="flex flex-col items-start w-full">
                                    <div class="w-full text-left">
                                        <div class="relative w-full overflow-y-auto text-sm font-normal leading-5">
                                            <slot />
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="flex justify-end flex-shrink-0 w-full px-4 py-3 bg-gray-100 sm:px-6">
                                <slot name="formButtons" />
                            </div>
                            <div class="absolute top-0 right-0 block pt-4 pr-4">
                                <button
                                    type="button"
                                    class="text-gray-400 bg-white rounded-md hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
                                    tabindex="0"
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

<script lang="ts" setup>
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { AnySchema } from "yup";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";

interface IAiModalProps {
    dialog: boolean;
    title?: string;
    description?: string;
    small?: boolean;
    schema: AnySchema;
    submit: (...args: unknown[]) => Promise<void>;
}

withDefaults(
    defineProps<IAiModalProps>(),
    {
        title: undefined,
        description: undefined
    }
);

const emit = defineEmits(["closeModal"]);

const close = () => {
    emit("closeModal", false);
};
</script>
