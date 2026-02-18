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
    <div v-tippy="tooltip">
        <template v-if="!!link">
            <router-link
                v-slot="{ navigate }"
                custom
                :to="link ?? ''"
            >
                <button
                    class="relative flex flex-row justify-center btn-base"
                    :type="type"
                    :data-test="dataTest"
                    :class="{
                        'btn-sm': small,
                        'btn-lg': large,
                        'btn-primary': primary,
                        'btn-text': text,
                        'btn-light': light,
                        'btn-disabled': loading || disabled,
                        'btn-text-primary': textPrimary,
                        'btn-text-secondary': textSecondary,
                        'btn-text-error': error,
                        'btn-block': block
                    }"
                    :tabindex="tabindex"
                    @click="navigate"
                >
                    <slot />
                </button>
            </router-link>
        </template>
        <template v-else>
            <button
                class="relative flex flex-row justify-center btn-base"
                :type="type"
                :data-test="dataTest"
                :class="{
                    'btn-sm': small,
                    'btn-lg': large,
                    'btn-primary': primary,
                    'btn-text': text,
                    'btn-light': light,
                    'btn-clear': clear,
                    'btn-disabled': loading || disabled,
                    'btn-text-primary': textPrimary,
                    'btn-text-secondary': textSecondary,
                    'btn-text-error': error,
                    'btn-block': block
                }"
                :disabled="loading || disabled"
                :tabindex="tabindex"
                @click.capture="() => !link && clickButton()"
            >
                <div v-if="loading" class="absolute">
                    <svg
                        class="w-5 h-5 text-white animate-spin"
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
                </div>
                <slot />
            </button>
        </template>
    </div>
</template>

<script setup lang="ts">
import { directive as vTippy } from "vue-tippy";

interface IAiButtonProps {
    text?: boolean;
    textPrimary?: boolean;
    textSecondary?: boolean;
    primary?: boolean;
    light?: boolean;
    error?: boolean;
    small?: boolean;
    large?: boolean;
    clear?: boolean;
    loading?: boolean;
    block?: boolean;
    disabled?: boolean;
    dataTest?: string;
    type?: "button" | "submit" | "reset" | undefined;
    tooltip?: string;
    link?: string;
    tabindex?: number;
}

withDefaults(
    defineProps<IAiButtonProps>(),
    {
        dataTest: "btn",
        type: "button",
        tooltip: "",
        link: undefined,
        tabindex: 0
    }
);

const emit = defineEmits(["click"]);

const clickButton = () => {
    emit("click");
};

</script>

<style lang="css" scoped>
.btn-base {
    @apply inline-flex items-center px-3 py-2 border border-gray-300 dark:border-gray-600 shadow-sm text-sm;
    @apply ring-offset-2 dark:ring-offset-gray-900 bg-white dark:bg-gray-900 hover:bg-gray-100 dark:hover:bg-gray-800 transition duration-300 rounded text-gray-700 dark:text-gray-400;
    @apply focus:ring-2 focus:ring-offset-2 focus:ring-primary-500 focus:bg-gray-50 dark:focus:ring-primary-400 dark:focus:bg-gray-900 focus:outline-none;
    @apply truncate whitespace-nowrap font-bold leading-5 overflow-ellipsis;
}

.btn-base.btn-sm {
    @apply px-2.5 py-1.5 text-sm;
}

.btn-base.btn-lg {
    @apply px-4 py-2 text-lg;
}

.btn-base.btn-text {
    @apply border-0 shadow-none bg-transparent outline-none focus:ring-0 focus:bg-none focus:shadow-none focus:ring-offset-0 dark:hover:bg-transparent;
}

.btn-base.btn-text-primary {
    @apply text-primary-500 dark:text-primary-200 hover:text-primary-800;
}

.btn-base.btn-text-secondary {
    @apply border-0 shadow-none bg-transparent outline-none focus:ring-0 focus:bg-none focus:shadow-none focus:ring-offset-0 dark:hover:bg-gray-800 hover:bg-gray-100;
}

.btn-base.btn-text-error {
    @apply text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-500;
}

.btn-base.btn-primary {
    @apply border-0 text-white dark:text-primary-200 bg-primary-500 hover:bg-primary-400 focus:bg-primary-500 dark:hover:bg-primary-600 dark:focus:bg-primary-500;
}

.btn-base.btn-light {
    @apply border-0 text-primary-600 dark:text-gray-100 dark:bg-gray-700 dark:hover:bg-gray-800 bg-primary-200/70 hover:bg-primary-200 focus:bg-primary-200/70;
    @apply disabled:opacity-40 disabled:text-opacity-40 disabled:bg-primary-200 dark:disabled:bg-gray-900;
}

.btn-base.btn-clear {
    @apply bg-transparent dark:hover:bg-gray-700 hover:bg-primary-200 focus:bg-primary-200/70 dark:text-primary-200 p-[calc(0.5rem_-_1px)];
}

.btn-base.btn-disabled {
    @apply text-opacity-70 hover:bg-opacity-60 bg-opacity-40;
    @apply cursor-not-allowed transition;
}

.btn-base.btn-block {
    @apply w-full;
}
</style>
