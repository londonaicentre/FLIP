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
    <div>
        <div class="relative">
            <div class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                <icon-mdi-magnify class="w-5 h-5 text-gray-400 dark:text-gray-600" />
            </div>
            <input
                :value="modelValue"
                type="search"
                :placeholder="placeholder"
                :data-test="dataTest"
                class="block w-full pl-10 text-sm transition duration-300 border-gray-300 dark:border-gray-700 dark:bg-gray-800 rounded-md shadow-sm focus:border-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400 focus:ring-primary-500 focus:ring-1 dark:text-gray-300"
                @input="update"
            >
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";

export default defineComponent({
    name: "AiSearch",
    props: {
        modelValue: {
            type: String,
            default: ""
        },
        placeholder: {
            type: String,
            default: ""
        },
        dataTest: {
            type: String,
            default: ""
        }
    },
    emits: ["update:modelValue"],
    methods: {
        update(event: Event) {
            return this.$emit("update:modelValue", (event.target as HTMLInputElement).value);
        }
    }
});
</script>
