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
        class="block w-full px-4 py-2 bg-red-50 dark:bg-red-800"
    >
        <div class="flex items-center justify-center">
            <div class="flex-shrink-0">
                <icon-ic-twotone-cancel
                    class="w-5 h-5 text-red-500 dark:text-red-200"
                />
            </div>
            <div class="flex-1 ml-3 md:flex md:justify-between ">
                <p
                    class="text-sm font-semibold text-red-500 dark:text-red-200"
                >
                    {{ text }}
                </p>
            </div>
            <div class="flex-shrink-0">
                <AiButton
                    text
                    small
                    class="group"
                    title="Close"
                    @click="closeBanner"
                >
                    <icon-mdi-close
                        class="w-5 h-5 text-red-500 dark:text-red-200 dark:group-hover:text-red-100 transition group-hover:text-red-800"
                    />
                </AiButton>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">

import { useErrorStore } from "@/store/error";

import AiButton from "../AiButton/AiButton.vue";

interface IAiErrorAlertProps {
    text?: string;
}

withDefaults(
    defineProps<IAiErrorAlertProps>(),
    { text: "Something went wrong, please try again later. If the issue persists please contact the service desk." }
);

const errorStore = useErrorStore();

const closeBanner = () => {
    errorStore.clearError();
};
</script>
