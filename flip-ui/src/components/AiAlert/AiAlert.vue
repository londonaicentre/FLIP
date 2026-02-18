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

<!-- eslint-disable max-len -->
<template>
    <div
        v-if="show"
        class="block w-full p-2"
        :class="[
            rounded && 'rounded-md',
            variant === 'success' && 'bg-lightgreen-100/50 border border-emerald-600/20 dark:bg-green-700 dark:border-green-600',
            variant === 'info' && 'bg-blue-50 border border-blue-200 dark:bg-blue-800 dark:border-blue-600',
            variant === 'error' && 'bg-red-50 border border-red-200 dark:bg-red-800 dark:border-red-600',
            variant === 'warning' && 'bg-yellow-50 border border-yellow-200 dark:bg-yellow-900 dark:border-yellow-600',
            !bordered && 'border-none'
        ]"
    >
        <div class="flex">
            <div v-if="showIcon" class="flex-shrink-0 mr-3">
                <icon-ic-twotone-info v-if="variant === 'info'" class="w-5 h-5 text-blue-500 dark:text-blue-200" />
                <icon-ic-twotone-warning v-if="variant === 'warning'" class="w-5 h-5 text-yellow-600 dark:text-yellow-300" />
                <icon-ic-twotone-check-circle v-if="variant === 'success'" class="w-5 h-5 text-lightgreen-900 dark:text-green-200" />
                <icon-ic-twotone-cancel v-if="variant === 'error'" class="w-5 h-5 text-red-500 dark:text-red-200" />
            </div>
            <div class="items-center justify-center flex-1 md:flex md:justify-between">
                <p
                    class="text-sm font-semibold"
                    :class="[
                        variant === 'success' && 'text-lightgreen-900 dark:text-green-200',
                        variant === 'info' && 'text-blue-600 dark:text-blue-200',
                        variant === 'error' && 'text-red-500 dark:text-red-200',
                        variant === 'warning' && 'text-yellow-700 dark:text-yellow-300']"
                >
                    <span v-html="text" />
                </p>
                <div v-if="!!actionText" class="flex">
                    <p class="mt-3 text-sm md:mt-0 md:ml-6">
                        <span
                            class="font-black cursor-pointer whitespace-nowrap"
                            :class="[
                                variant === 'success' && 'text-lightgreen-900 hover:text-lightgreen-900/70 dark:text-green-200 dark:hover:text-green-300',
                                variant === 'info' && 'text-blue-500 hover:text-blue-600 dark:text-blue-200 dark:hover:text-blue-300',
                                variant === 'error' && 'text-red-500 hover:text-red-600 dark:text-red-200 dark:hover:text-red-300',
                                variant === 'warning' && 'text-yellow-700 hover:text-yellow-600 dark:text-yellow-300 dark:hover:text-yellow-400']"
                            @click.capture="performAction"
                        >
                            {{ actionText }}
                        </span>
                    </p>
                </div>
                <div v-if="close" class="flex pl-3 ml-auto">
                    <div class="-mx-1.5 -my-1.5">
                        <button
                            type="button"
                            class="inline-flex rounded-md p-1.5"
                            :class="[
                                variant === 'success' && 'text-green-500 hover:text-green-600 dark:text-green-200 dark:hover:text-green-300',
                                variant === 'info' && 'text-blue-500 hover:text-blue-600 dark:text-blue-200 dark:hover:text-blue-300',
                                variant === 'error' && 'text-red-500 hover:text-red-600 dark:text-red-200 dark:hover:text-red-300',
                                variant === 'warning' && 'text-yellow-700 hover:text-yellow-600 dark:text-yellow-300 dark:hover:text-yellow-400']"
                            @click="remove"
                        >
                            <span class="sr-only">Dismiss</span>
                            <icon-mdi-close />
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script lang="ts" setup>
import { ref } from "vue";

interface IAiAlertProps {
    text: string;
    variant: "success" | "info" | "error" | "warning",
    actionText?: string;
    close?: boolean;
    showIcon?: boolean;
    rounded?: boolean;
    bordered?: boolean;
}

withDefaults(
    defineProps<IAiAlertProps>(),
    {
        showIcon: true,
        actionText: undefined,
        close: false,
        rounded: true,
        bordered: true
    }
);

const emits = defineEmits(["action"]);

const show = ref(true);

const performAction = () => {
    emits("action");
};

const remove = () => {
    show.value = false;
};

</script>
