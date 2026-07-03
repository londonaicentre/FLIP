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
        class="flex flex-row items-center h-14 bg-white dark:bg-dark-canvas border-b border-gray-200 dark:border-dark-border px-4 xl:px-6 shrink-0"
    >
        <!-- Mobile menu button -->
        <PopoverButton class="outline-none xl:hidden mr-3" data-test="mobile-menu-btn">
            <icon-heroicons-outline-menu-alt-1 class="w-6 h-6" />
        </PopoverButton>

        <!-- Logo (left) — London AI Centre + FLIP marks -->
        <router-link to="/" class="flex items-center gap-3 flex-shrink-0 mr-4 xl:mr-9">
            <img src="/images/aicentre-logo-transparent.webp" alt="London AI Centre" class="h-12 w-auto">
            <img src="/images/flip-logo-icon.webp" alt="FLIP" class="h-8 w-auto">
        </router-link>

        <!-- Vertical divider (desktop) -->
        <div class="hidden xl:block w-px h-6 bg-gray-200 dark:bg-dark-raised mr-2" />

        <!-- Desktop top-nav (hidden on mobile — uses popover instead) -->
        <nav
            class="hidden xl:flex flex-row items-stretch h-full"
            aria-label="Primary"
            data-test="top-nav"
        >
            <router-link
                v-for="item in navigation"
                :key="item.name"
                :to="item.href"
                class="relative inline-flex items-center px-4 text-sm font-semibold transition-colors"
                :class="item.current
                    ? 'text-gray-900 dark:text-gray-100'
                    : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
            >
                {{ item.name }}
                <span
                    v-if="item.current"
                    aria-hidden="true"
                    class="absolute left-3 right-3 -bottom-px h-[3px] bg-primary-500 rounded-t"
                />
            </router-link>
        </nav>

        <!-- Spacer -->
        <div class="flex-grow" />

        <!-- Deployment-mode badge + right-side slot (user dropdown) -->
        <div class="flex flex-row items-center justify-end h-full">
            <div
                v-if="useSiteDetailsStore().deploymentMode"
                v-tippy="{ placement: 'bottom-end', content: 'Platform updates are in progress' }"
                data-test="deployment-mode-status"
                class="relative w-16 mr-6 bg-gray-100 dark:bg-dark-raised px-3.5 py-2 rounded-md flex items-center justify-center"
            >
                <span class="absolute top-0 right-0 flex items-center justify-center w-3 h-3 -mt-1 -mr-1">
                    <span class="absolute inline-flex w-full h-full bg-green-300 rounded-full animate-ping" />
                    <span class="relative inline-flex w-2 h-2 bg-green-500 rounded-full" />
                </span>
                <icon-ph-download-duotone class="w-6 h-6" />
            </div>
            <slot />
        </div>

        <!-- MOBILE MENU (popover panel, unchanged behavior) -->
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
                            class="bg-white divide-y divide-gray-200 rounded-lg shadow-lg dark:bg-dark-canvas dark:divide-dark-border dark:ring-white/20 ring-1 ring-black ring-opacity-5"
                        >
                            <div class="pt-3 pb-2 divide-y dark:divide-dark-border">
                                <div class="flex items-start justify-between px-4">
                                    <div class="flex items-center gap-3">
                                        <img src="/images/aicentre-logo-transparent.webp" alt="London AI Centre" class="h-[50px] w-auto">
                                        <img src="/images/flip-logo-icon.webp" alt="FLIP" class="h-[42px] w-auto">
                                    </div>
                                    <div class="-mr-2">
                                        <PopoverButton
                                            class="inline-flex items-center justify-center p-2 rounded-md text-primary-500 dark:text-primary-400 hover:text-primary-800 hover:bg-gray-100 dark:hover:bg-dark-surface focus:outline-none"
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
                                        :class="[
                                            item.current
                                                ? 'bg-gray-100 font-semibold dark:bg-dark-surface dark:text-gray-300 dark:hover:text-gray-300'
                                                : 'text-gray-700 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 dark:hover:bg-dark-surface font-medium'
                                        ]"
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
import { Popover, PopoverButton, PopoverPanel, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { directive as vTippy } from "vue-tippy";

import AiPopoverOverlay from "@/components/AiPopoverOverlay/AiPopoverOverlay.vue";
import useNavigation from "@/composables/navigation";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

export interface IAIHeaderProps {
    // Kept for backwards-compat with callers that still pass `:title`. The
    // page itself owns its title now (rendered inside the page body), so this
    // prop is read but not displayed — removing it later is a separate clean-up
    // touching every layout that mounts AiHeader.
    title: string;
    currentPage: string;
    isDark: boolean;
}

const props = defineProps<IAIHeaderProps>();

const navigation = useNavigation(props);
</script>
