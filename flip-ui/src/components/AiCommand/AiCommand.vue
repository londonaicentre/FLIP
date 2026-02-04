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
    <TransitionRoot :show="open" as="template" appear class="fixed">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-y-auto" @close="close">
            <div class="absolute inset-0 overflow-hidden">
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


                <div class="fixed inset-0 z-10 inline-flex items-center min-h-screen p-4 overflow-y-auto align-middle sm:p-6 md:p-20">
                    <TransitionChild
                        as="template"
                        enter="ease-out duration-300"
                        enter-from="opacity-0 scale-95"
                        enter-to="opacity-100 scale-100"
                        leave="ease-in duration-200"
                        leave-from="opacity-100 scale-100"
                        leave-to="opacity-0 scale-95"
                    >
                        <DialogPanel
                            class="w-full max-w-2xl max-h-screen p-4 mx-auto overflow-hidden align-middle transition-all transform shadow-xl rounded-xl dark:ring-1 dark:ring-white/20"
                            :class="light ? 'bg-gray-100' : 'bg-gray-800'"
                        >
                            <button class="absolute p-1 border border-gray-600 rounded top-4 right-4 group" @click="close">
                                <icon-mdi-close class="text-gray-300 transition group-hover:text-gray-100" />
                            </button>
                            <slot />
                        </DialogPanel>
                    </TransitionChild>
                </div>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog,
    DialogPanel,
    TransitionChild,
    TransitionRoot } from "@headlessui/vue";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";

interface IAiCommandProps {
    open: boolean;
    light?: boolean;
}

defineProps<IAiCommandProps>();

const emits = defineEmits(["close"]);

const close = () => {
    emits("close");
};

</script>
