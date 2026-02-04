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

<!-- eslint-disable vue/multi-word-component-names -->
<template>
    <div class="relative flex items-center group">
        <div class="flex-1 min-w-0 text-sm">
            <label :for="uuid" class="font-bold cursor-pointer dark:text-gray-300">
                {{ label }}
                <p class="font-medium text-gray-500 dark:text-gray-400" :for="uuid">
                    {{ description }}
                </p>
            </label>
        </div>
        <div class="flex items-center h-5 ml-3">
            <input
                :id="uuid"
                aria-describedby="comments-description"
                data-test="filter-input"
                :name="uuid"
                :checked="checked"
                type="checkbox"
                class="w-5 h-5 transition border-gray-400 rounded cursor-pointer dark:border-gray-500 dark:bg-gray-600 group-hover:ring-2 group-hover:ring-offset-2 group-hover:ring-primary-500 text-primary-600 dark:text-primary-400 focus:ring-primary-500 dark:ring-offset-gray-800 dark:group-hover:ring-primary-400 dark:focus:ring-primary-400"
                @change="filterChanged"
            >
        </div>
    </div>
</template>

<script lang="ts" setup>
import { getRandomId } from "@/utils/helpers";


interface IProjectFilterProps {
    label: string;
    description: string;
    checked: boolean;
}

const props = defineProps<IProjectFilterProps>();

const emits = defineEmits(["filter-updated"]);

const uuid = getRandomId();

const filterChanged = () => {
    emits("filter-updated", !props.checked);
};
</script>
