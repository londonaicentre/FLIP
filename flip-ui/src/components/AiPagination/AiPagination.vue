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
    <div class="min-w-max">
        <section class="flex justify-between py-2 text-gray-700 border-t border-gray-200 dark:border-gray-700 bg-body dark:bg-gray-900">
            <div class="flex items-center justify-center w-full">
                <div class="pr-4">
                    <AiButton text :disabled="!hasPrev()" data-test="page-btn-prev" @click="() => goToPage(prevPage)">
                        <icon-ic-arrow-back class="mr-2" />
                        <template v-if="!slim">
                            Previous
                        </template>
                    </AiButton>
                </div>
                <div class="flex items-center justify-center flex-grow">
                    <div v-if="hasFirst()" class="pr-2">
                        <div :class="{'selected-border': currentPage === 1}" />
                        <AiButton
                            text
                            data-test="page-btn-1"
                            :class="{'text-primary-500 dark:text-primary-400': currentPage === 1}"
                            @click="() => goToPage(1)"
                        >
                            1
                        </AiButton>
                    </div>
                    <div v-if="hasFirst()" class="pr-2">
                        ...
                    </div>
                    <div v-for="page in pages" :key="page" class="flex flex-col pr-2">
                        <div :class="{'selected-border': currentPage === page}" />
                        <AiButton
                            text
                            :data-test="`page-btn-${page}`"
                            :class="{'text-primary-500 dark:text-primary-400': currentPage === page}"
                            @click="() => goToPage(page)"
                        >
                            {{ page }}
                        </AiButton>
                    </div>
                    <div v-if="hasLast()" class="pr-2">
                        ...
                    </div>
                    <div v-if="hasLast()" class="pr-2">
                        <div :class="{'selected-border': currentPage === totalPages}" />
                        <AiButton
                            text
                            :data-test="`page-btn-${totalPages}`"
                            :class="{'text-primary-500 dark:text-primary-400': currentPage === totalPages}"
                            @click="() => goToPage(totalPages)"
                        >
                            {{ totalPages }}
                        </AiButton>
                    </div>
                </div>
                <div>
                    <AiButton text data-test="page-btn-next" :disabled="!hasNext()" @click="() => goToPage(nextPage)">
                        <template v-if="!slim">
                            Next
                        </template>
                        <icon-ic-arrow-forward class="ml-2" />
                    </AiButton>
                </div>
            </div>
        </section>
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";

/**
 * Props
 */
interface IPaginationProps {
    totalPages: number;
    currentPage: number;
    slim?: boolean;
}

const props = withDefaults(
    defineProps<IPaginationProps>(),
    {
        currentPage: 1,
        totalPages: 1
    }
);

const emit = defineEmits(["pageUpdate"]);

/** The number of pages to display either side of the currently selected page */
const pageRange = props.slim ? 0 : 2;

/**
 * Methods
 */

const goToPage = (targetPage: number) => {
    emit("pageUpdate", targetPage);
};

const hasFirst = () => {
    return rangeStart.value !== 1;
};

const hasLast = () => {
    return rangeEnd.value < props.totalPages;
};

const hasPrev = () => {
    return props.currentPage > 1;
};

const hasNext = () => {
    return props.currentPage < props.totalPages;
};

/**
 * Computed
 */

const pages = computed(() => {
    const pages = [];
    let i: number = rangeStart.value;

    for (i; i <= rangeEnd.value; i++) {
        pages.push(i);
    }

    return pages;
});

const rangeStart = computed(() => {
    const start = props.currentPage - pageRange;

    return (start > 2) ? start : 1;
});

const rangeEnd = computed(() => {
    const end = props.currentPage + pageRange;

    return (end < props.totalPages - 1) ? end : props.totalPages;
});

const nextPage = computed(() => {
    return props.currentPage + 1;
});

const prevPage = computed(() => {
    return props.currentPage - 1;
});
</script>

<style scoped>
.selected-border {
    @apply border-t-2 border-primary-500 dark:border-primary-400 h-full w-full mt-[-9px] pb-[7px]
}
</style>
