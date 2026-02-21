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
    <Popover
        v-slot="{ open, close }"
        as="header"
        class="flex flex-row items-center py-0 xl:py-3.5 w-full align-middle bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 px-4 shrink-0"
    >
        <div class="flex items-center flex-shrink-0 h-full xl:hidden">
            <PopoverButton class="outline-none">
                <icon-heroicons-outline-menu-alt-1 />
            </PopoverButton>
        </div>

        <div class="w-[120px] xl:hidden h-full items-center justify-center flex flex-shrink-0 py-2">
            <router-link to="/">
                <logo v-if="!isDark" class="h-[50px] w-auto mx-auto" />
                <logoDark v-else class="h-[50px] w-auto mx-auto" />
            </router-link>
        </div>

        <div class="w-[100px] xl:hidden h-full items-center justify-center flex flex-shrink-0">
            <img :src="nhsLogo" class="mx-auto rounded h-1/2">
        </div>

        <div class="flex-row items-center justify-center flex-grow hidden h-full xl:flex">
            <div class="flex-grow text-2xl font-semibold font-heading " data-test="header-title">
                {{ title }}
            </div>
        </div>
        <div class="flex flex-row items-center justify-end h-full grow xl:flex-grow-0">
            <div
                v-if="useSiteDetailsStore().deploymentMode"
                v-tippy="{placement: 'bottom-end', content: 'Platform updates are in progress'}"
                data-test="deployment-mode-status"
                class="relative w-16 mr-6 bg-gray-100 dark:bg-gray-700 px-3.5 py-2 rounded-md flex items-center justify-center"
            >
                <span class="absolute top-0 right-0 flex items-center justify-center w-3 h-3 -mt-1 -mr-1">
                    <span class="absolute inline-flex w-full h-full bg-green-300 rounded-full animate-ping" />
                    <span class="relative inline-flex w-2 h-2 bg-green-500 rounded-full" />
                </span>
                <icon-ph-download-duotone class="w-6 h-6" />
            </div>
            <slot />
        </div>

        <!-- MOBILE MENU -->
        <TransitionRoot as="template" :show="open">
            <div class="xl:hidden">
                <TransitionChild
                    as="template"
                    enter="duration-150 ease-out"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="duration-150 ease-in"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiPopoverOverlay />
                </TransitionChild>

                <TransitionChild
                    as="template"
                    enter="duration-150 ease-out"
                    enter-from="opacity-0 scale-95"
                    enter-to="opacity-100 scale-100"
                    leave="duration-150 ease-in"
                    leave-from="opacity-100 scale-100"
                    leave-to="opacity-0 scale-95"
                >
                    <PopoverPanel
                        focus
                        class="absolute inset-x-0 top-0 z-30 w-full max-w-3xl p-2 mx-auto transition origin-top transform"
                    >
                        <div
                            class="bg-white divide-y divide-gray-200 rounded-lg shadow-lg dark:bg-gray-900 dark:divide-gray-700 dark:ring-white/20 ring-1 ring-black ring-opacity-5"
                        >
                            <div class="pt-3 pb-2 divide-y dark:divide-gray-700">
                                <div class="flex items-start justify-between px-4">
                                    <div>
                                        <logo v-if="!isDark" class="h-[50px] w-auto mx-auto" />
                                        <logoDark v-else class="h-[50px] w-auto mx-auto" />
                                    </div>
                                    <div class="-mr-2">
                                        <PopoverButton
                                            class="inline-flex items-center justify-center p-2 rounded-md text-primary-500 dark:text-primary-400 hover:text-primary-800 hover:bg-gray-100 dark:hover:bg-gray-800 focus:outline-none"
                                        >
                                            <span class="sr-only">Close menu</span>
                                            <icon-heroicons-outline-x class="w-5 h-5" aria-hidden="true" />
                                        </PopoverButton>
                                    </div>
                                </div>
                                <div class="px-2 py-3 mt-3 space-y-2">
                                    <router-link
                                        v-for="item in navigation"
                                        :key="item.name"
                                        :to="item.href"
                                        :class="[{ 'bg-gray-100 font-semibold dark:bg-gray-800 dark:text-gray-300 dark:hover:text-gray-300': item.current },
                                                 { 'text-gray-700 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 dark:hover:bg-gray-800 font-medium': !item.current }]"
                                        class="block px-3 py-2 text-base rounded-md"
                                        @click="close"
                                    >
                                        {{ item.name }}
                                    </router-link>
                                </div>
                            </div>
                        </div>
                    </PopoverPanel>
                </TransitionChild>
            </div>
        </TransitionRoot>
    </Popover>
</template>

<script setup lang="ts">
import { Popover,
    PopoverButton,
    PopoverPanel,
    TransitionChild,
    TransitionRoot } from "@headlessui/vue";

import nhsLogo from "/images/nhs-logo.png";
import logo from "@/assets/logo.svg?component";
import logoDark from "@/assets/logo_dark.svg?component";
import AiPopoverOverlay from "@/components/AiPopoverOverlay/AiPopoverOverlay.vue";
import useNavigation from "@/composables/navigation";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

export interface IAIHeaderProps {
    title: string;
    currentPage: string;
    isDark: boolean;
}

const props = defineProps<IAIHeaderProps>();

const navigation = useNavigation(props);
</script>
