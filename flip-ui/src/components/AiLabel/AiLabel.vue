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
    <label
        class="inline-flex items-center justify-center h-full gap-2 p-4 pt-1 pb-1 text-sm rounded-md"
        small
        :class="{
            'label-base': base,
            'label-info': info,
            'label-success': success,
            'label-error': error,
        }"
    >
        <slot name="preText" />
        {{ text }}
        <slot name="postText" />

    </label>
</template>

<script lang="ts">
import { defineComponent } from "vue";

export default defineComponent({
    name: "AiLabel",
    props: {
        text: {
            type: String,
            required: true
        },
        base: {
            type: Boolean,
            required: false,
            default: true
        },
        info: {
            type: Boolean,
            required: false,
            default: false
        },
        success: {
            type: Boolean,
            required: false,
            default: false
        },
        error: {
            type: Boolean,
            required: false,
            default: false
        }
    }
});
</script>

<style lang="css" scoped>
.label-base {
    @apply bg-gray-50 text-gray-700 dark:bg-gray-600 dark:text-gray-100 font-semibold;
}

.label-info {
    @apply bg-amber-50 text-deeporange-900 font-semibold dark:bg-blue-700 dark:text-blue-200;
}

.label-success {
    @apply bg-lightgreen-100/30 text-lightgreen-900 font-semibold dark:bg-green-800 dark:text-green-100;
}

.label-error {
    @apply bg-red-50 text-red-800 dark:bg-red-800 dark:text-red-200 font-semibold;
}
</style>
