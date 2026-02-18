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
    <div class="flex items-center justify-end">
        <SwitchGroup>
            <SwitchLabel v-if="label" class="mr-4 text-sm">
                {{ checked ? label.enabled : label.disabled }}
            </SwitchLabel>
            <Switch
                v-if="!disabled"
                :id="uuid"
                :name="name"
                :model="checked"
                :data-test="dataTest"
                class="relative inline-flex items-center h-6 transition-colors rounded-full w-11 focus:outline-none focus:ring-2 focus:ring-primary-500 dark:focus:ring-primary-400 focus:ring-offset-2 dark:ring-offset-gray-900"
                :class="[
                    !!errorMessage && 'ring-2 ring-offset-2 ring-red-500 focus:ring-red-500 text-red-500 dark:ring-red-400 dark:text-red-400',
                    !!errorMessage && 'focus:border-red-500 border-red-500',
                    checked ? 'bg-primary-600 dark:bg-primary-400' : 'bg-gray-300 dark:bg-gray-600'
                ]"
                @click.capture="() => {
                    if (disabled) { return; }
                    handleChange(value)
                }"
            >
                <span
                    v-if="!disabled"
                    :class="[checked ? 'translate-x-6' : 'translate-x-1']"
                    class="inline-flex items-center justify-center w-4 h-4 transition-transform transform bg-white dark:bg-gray-200 rounded-full"
                >
                    <icon-heroicons-outline-check v-if="checked" class="w-3 h-3 text-green-600 dark:text-green-600" />
                </span>
            </Switch>
        </SwitchGroup>
    </div>
    <div v-if="!!errorMessage && !hideError" class="m-2 text-sm text-red-500 dark:text-red-400 error_message">
        {{ errorMessage }}
    </div>
</template>

<script lang="ts" setup>
import { Switch, SwitchGroup, SwitchLabel } from "@headlessui/vue";
import { useField } from "vee-validate";
import { toRefs } from "vue";

import { getRandomId } from "@/utils/helpers";

interface IAiSwitchProps {
    required?: boolean;
    disabled?: boolean;
    error?: string;
    label?: {
        enabled: string;
        disabled: string;
    },
    name: string;
    hint?: string;
    dataTest?: string;
    inputProps?: HTMLInputElement;
    value: string;
    hideError?: boolean;
}

const props = withDefaults(
    defineProps<IAiSwitchProps>(),
    {
        required: false,
        error: "",
        label: undefined,
        hint: undefined,
        dataTest: "",
        inputProps: undefined,
        hideError: false,
        disabled: false
    }
);

const { name, value } = toRefs(props);

const {
    checked,
    errorMessage,
    handleChange
} = useField(
    name,
    undefined,
    {
        type: "checkbox",
        checkedValue: value,
        uncheckedValue: undefined,
        validateOnMount: false
    });

const uuid = getRandomId();
</script>
