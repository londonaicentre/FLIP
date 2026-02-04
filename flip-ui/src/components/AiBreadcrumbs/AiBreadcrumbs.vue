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
    <nav
        id="breadcrumbs"
        class="relative hidden p-2 px-4 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 md:flex"
        :class="[!isAtTop && 'transition shadow-sm']"
        aria-label="Breadcrumb"
    >
        <div class="flex items-center min-w-0 space-x-2 truncate">
            <div>
                <div>
                    <router-link to="/" class="text-gray-400 hover:text-gray-500">
                        <icon-ph-house-line-duotone class="flex-shrink-0 w-5 h-5" aria-hidden="true" />
                        <span class="sr-only">Home</span>
                    </router-link>
                </div>
            </div>
            <div v-for="page in pages" :key="page.name" class="max-w-[250px]">
                <div class="flex items-center">
                    <icon-mdi-chevron-right class="flex-shrink-0 w-5 h-5 text-gray-400" aria-hidden="true" />
                    <router-link
                        v-tippy="{ content: page.name, placement: 'bottom-start' }"
                        :to="page.path"
                        class="ml-2 text-sm font-medium text-gray-500 truncate hover:text-gray-700 dark:hover:text-gray-400"
                        data-test="parent-page-text"
                    >
                        {{ page.name }}
                    </router-link>
                </div>
            </div>
            <div class="flex items-center max-w-[250px]">
                <icon-mdi-chevron-right class="flex-shrink-0 w-5 h-5 text-gray-400" aria-hidden="true" />
                <div
                    v-if="current.name"
                    v-tippy="{ content: current.name, placement: 'bottom-start' }"
                    class="ml-2 text-sm font-bold text-gray-700 dark:text-gray-400 truncate"
                    data-test="current-page-text"
                >
                    {{ current.name }}
                </div>
                <span v-else class="ml-2 text-sm font-bold text-gray-700">
                    <AiSkeleton class="!w-20 h-4" />
                </span>
            </div>
        </div>
    </nav>
</template>

<script lang="ts" setup>
import { useScroll } from "@vueuse/core";
import { onMounted, ref, watch } from "vue";
import { directive as vTippy } from "vue-tippy";

import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";

export interface IPage {
    name: string;
    path: string;
}

export interface ICurrentPage {
    name?: string;
}

interface IBreadcrumbProps {
    pages: IPage[];
    current: ICurrentPage;
}

const isAtTop = ref(true);

defineProps<IBreadcrumbProps>();

onMounted(() => {
    const { arrivedState } = useScroll(document.getElementById("breadcrumbs")?.nextElementSibling as HTMLElement);

    watch(arrivedState, () => {
        isAtTop.value = arrivedState.top;
    });

});

</script>
