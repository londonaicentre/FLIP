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
    <div
        class="flex flex-col flex-grow h-full pb-4 overflow-x-hidden overflow-y-auto bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700"
    >
        <div class="h-[80px] flex items-center justify-center">
            <router-link to="/">
                <logo v-if="!isDark" class="h-[50px] w-auto mx-auto" />
                <logoDark v-else class="h-[50px] w-auto mx-auto" />
            </router-link>
        </div>
        <div class="flex flex-col flex-grow mt-4">
            <nav class="flex-1 pr-3 space-y-1.5 bg-white dark:bg-gray-900" aria-label="Sidebar">
                <router-link
                    v-for="item in navigation"
                    :key="item.name"
                    :to="item.href"
                    :class="[{ 'bg-primary-500 dark:bg-primary-600 text-white dark:text-primary-200': item.current },
                             { 'text-gray-700 dark:text-gray-400': !item.current }]"
                    class="relative flex items-center py-2 pl-4 text-sm font-semibold transition group rounded-r-md hover:text-white hover:bg-primary-500 dark:hover:bg-primary-600 dark:hover:text-primary-200"
                >
                    <span v-tippy="{ content: item.name }" class="absolute block w-full h-full xl:hidden left-2" />

                    <component :is="item.icon" class="flex-shrink-0 hidden w-5 h-5 mr-3 xl:block " />
                    <span class="hidden leading-tight xl:flex-1 xl:block">
                        {{ item.name }}
                    </span>
                </router-link>
            </nav>
        </div>
        <div class="hidden px-8 py-2 xl:block">
            <img :src="NhsLogo" position="center" class="rounded dark:ring-blue-600 dark:ring-4 dark:ring-offset-4 dark:ring-offset-gray-900">
        </div>
    </div>
</template>

<script setup lang="ts">
import { directive as vTippy } from "vue-tippy";

import NhsLogo from "/images/nhs-logo.png";
import logo from "@/assets/logo.svg?component";
import logoDark from "@/assets/logo_dark.svg?component";
import useNavigation from "@/composables/navigation";

export interface IMainNavigationProps {
    currentPage: string;
    isDark: boolean;
}

const props = defineProps<IMainNavigationProps>();

const navigation = useNavigation(props);
</script>
