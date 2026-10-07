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

<!-- Trust detail side drawer (design handoff option 1b, issue #901): per-container
     status / version / response for one trust, derived live from the same SWRV
     data the Connection Status table renders. Esc / scrim close come from the
     HeadlessUI Dialog; focus returns to the previously-focused element (the
     trigger rows are tabindex=0, so it lands back on the row). -->
<template>
    <TransitionRoot as="template" :show="show">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-hidden" :unmount="true" @close="emit('close')">
            <div class="absolute inset-0 overflow-hidden">
                <TransitionChild
                    as="template"
                    enter="ease-out duration-[250ms]"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-out duration-[250ms]"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>

                <!-- The panel div below must be the TransitionChild's single, unconditional
                     child (no v-if, no sibling comment nodes): HeadlessUI forwards props onto
                     exactly one element, and the page nulls `trust` in the same tick it flips
                     `show` off — a conditional panel would hand the leave transition a comment
                     vnode and throw. Content is guarded inside the panel instead. -->
                <div class="fixed inset-y-0 right-0 flex max-w-full pl-10">
                    <TransitionChild
                        as="template"
                        enter="transform transition ease-out duration-[250ms]"
                        enter-from="translate-x-full"
                        enter-to="translate-x-0"
                        leave="transform transition ease-out duration-[250ms]"
                        leave-from="translate-x-0"
                        leave-to="translate-x-full"
                    >
                        <div
                            data-test="drawer-panel"
                            class="w-screen max-w-[410px] flex flex-col h-full bg-white dark:bg-dark-surface
                            border-l border-gray-200 dark:border-dark-border shadow-2xl dark:ring-1 dark:ring-white/20"
                        >
                            <TrustDetailCard
                                v-if="displayTrust"
                                class="flex-1"
                                :trust="displayTrust"
                                :hub-version="hubVersion"
                            >
                                <template #title="{ trustName }">
                                    <DialogTitle
                                        class="font-heading font-semibold text-[19px] mt-1 text-gray-900 dark:text-gray-100 truncate"
                                    >
                                        {{ trustName }}
                                    </DialogTitle>
                                </template>
                                <template #actions>
                                    <button
                                        type="button"
                                        data-test="drawer-close"
                                        class="shrink-0 opacity-70 hover:opacity-100 transition rounded
                                        text-gray-600 dark:text-gray-300 focus:outline-none focus:ring-1
                                        focus:ring-primary-400"
                                        tabindex="0"
                                        @click="emit('close')"
                                    >
                                        <span class="sr-only">Close</span>
                                        <icon-ph-x class="w-5 h-5" />
                                    </button>
                                </template>
                            </TrustDetailCard>
                        </div>
                    </TransitionChild>
                </div>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { computed, ref, watch } from "vue";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import TrustDetailCard from "@/partials/connection/TrustDetailCard.vue";
import { IDerivedTrust } from "@/utils/connection-health";

// The page passes a row whose health was derived once for this refresh. Re-deriving
// here would take a second Date.now() and could land the drawer on the other side
// of a threshold, so the row and the drawer could contradict each other on screen.
const props = defineProps<{
    trust: IDerivedTrust | null;
    show: boolean;
    // The release the hub runs (FLIP#1204), from the page's hub-health poll; null until known
    // or on a hub built before it reported one. Only FLIP-built containers are compared
    // against it, and only when both sides carry an image tag — see TrustDetailCard.
    hubVersion?: string | null;
}>();

const emit = defineEmits<{ close: [] }>();

// The page nulls `trust` in the same tick it flips `show` off; keep the last
// non-null trust so the 250ms slide-out renders content, not an empty shell.
const lastTrust = ref<IDerivedTrust | null>(null);
watch(
    () => props.trust,
    t => {
        if (t) lastTrust.value = t;
    },
    { immediate: true }
);
const displayTrust = computed(() => props.trust ?? lastTrust.value);
</script>
