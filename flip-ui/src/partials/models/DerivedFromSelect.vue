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
    <div v-if="options.length">
        <label for="derived-from" class="block text-sm font-medium text-gray-700 dark:text-gray-200">
            Derived from
        </label>
        <select
            id="derived-from"
            data-test="derived-from-select"
            class="block w-full py-2 pl-3 pr-8 mt-1 text-sm text-gray-900 bg-white border border-gray-300 rounded-md shadow-sm dark:bg-gray-700 dark:text-gray-100 dark:border-gray-700 focus:border-primary-500 focus:ring-primary-500 disabled:opacity-60"
            :value="modelValue ?? ''"
            :disabled="disabled"
            @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value || null)"
        >
            <option value="">
                None
            </option>
            <option v-for="option in options" :key="option.id" :value="option.id">
                {{ option.name }}
            </option>
        </select>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-300">
            Optional. Another model of this project whose result this one was built from.
        </p>
    </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from "vue";

import { getModels, IModel } from "@/services/model-service";

interface IDerivedFromSelectProps {
    projectId: string;
    modelValue: string | null;
    // The model being edited, which cannot be derived from itself.
    excludeId?: string;
    disabled?: boolean;
}

const props = defineProps<IDerivedFromSelectProps>();
const emit = defineEmits<{ "update:modelValue": [value: string | null] }>();

const options = ref<IModel[]>([]);

onMounted(async () => {
    try {
        const { data } = await getModels(`/projects/${props.projectId}/models?pageSize=100`);
        options.value = data.filter(model => model.id !== props.excludeId);
    } catch {
        // The link is optional: without the list the form still saves, just without the field.
        options.value = [];
    }
});
</script>
