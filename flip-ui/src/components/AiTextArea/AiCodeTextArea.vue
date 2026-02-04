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
        <label
            :for="uuid"
            class="flex w-full gap-1 text-sm font-bold"
            :class="{ 'text-red-500': !!errorMessage, 'text-gray-700': !errorMessage }"
        >
            {{ label }}
        </label>
        <div class="mt-1">
            <div
                class="block"
                :class="{
                    'ring-1 ring-red-500 focus-within:ring-red-500 text-red-500': !!errorMessage,
                    'focus-within:border-red-500 border-red-500': !!errorMessage
                }"
            >
                <Codemirror
                    :id="uuid"
                    :options="cmOptions"
                    :name="name"
                    :value="inputValue"
                    :border="true"
                    :data-test="dataTest"
                    autocomplete="none"
                    :height="200"
                    v-bind="inputProps"
                    class="cursor-text text-gray-400 overflow-hidden rounded dark:!ring-primary-400 ring-primary-500 ring-2 ring-offset-2 dark:ring-offset-gray-900"
                    @input="handleChange"
                    @blur="handleBlur"
                />
            </div>
        </div>
        <div v-if="hint && !errorMessage" class="m-1 text-sm text-gray-500">
            {{ hint }}
        </div>
        <div v-if="!!errorMessage" class="m-1 text-sm text-red-500 error_message">
            {{ errorMessage }}
        </div>
    </div>
</template>

<script lang="ts" setup>
import "codemirror/mode/sql/sql.js";

import { useField } from "vee-validate";
import { computed, TextareaHTMLAttributes } from "vue";

import { useSiteSettings } from "@/store/siteSettingsStore";
import { getRandomId } from "@/utils/helpers";

interface ICodeTextAreaProps {
    name: string;
    label: string;
    required?: boolean;
    error?: string;
    value?: string;
    placeholder?: string;
    hint?: string;
    inputProps?: TextareaHTMLAttributes;
    dataTest?: string;
    initialValue?: string;
    mode?: string;
}

const siteSettings = useSiteSettings();

const props = withDefaults(
    defineProps<ICodeTextAreaProps>(),
    {
        required: false,
        dataTest: "",
        error: undefined,
        placeholder: "",
        value: "",
        hint: "",
        initialValue: "",
        mode: "text/x-pgsql",
        inputProps: undefined
    }
);

const {
    value: inputValue,
    errorMessage,
    handleBlur,
    handleChange
} = useField(props.name, undefined, { initialValue: props.initialValue });

const cmOptions = computed(() => ({
    mode: props.mode,
    theme: siteSettings.darkMode ? "dracula": "default",
    lineNumbers: true,
    smartIndent: true,
    indentUnit: 4,
    foldGutter: true,
    lineWrapping: true,
    styleActiveLine: true,
    readOnly: props.inputProps?.readonly ?? false,
    cursorBlinkRate: props.inputProps?.readonly ? -1 : 530 //blink rate of 530ms is the default, -1 will make it hidden
}));

const uuid = getRandomId();
</script>
