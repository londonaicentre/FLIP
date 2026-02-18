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
    <div class="flex flex-col items-center justify-center w-full gap-4 h-full shrink-0 min-h-fit">
        <svg
            class=" animate-spin"
            :class="[inverted ? 'text-gray-200' : 'text-primary-600 dark:text-gray-200', small ? 'w-4 h-4 min-w-4 min-h-4' : 'w-6 h-6 min-w-6 min-h-6']"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
        >
            <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
            />
            <path
                class=""
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
        </svg>
        <div v-if="message" class="text-center">
            {{ message }}
        </div>
    </div>
</template>

<script setup lang="ts">
interface IAiLoaderProps {
    message?: string;
    inverted?: boolean;
    small?: boolean;
}

defineProps<IAiLoaderProps>();
</script>
