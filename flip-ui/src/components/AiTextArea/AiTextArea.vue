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
            :class="{ 'text-red-500 dark:text-red-400': !!errorMessage, 'text-gray-700 dark:text-gray-400': !errorMessage }"
        >
            {{ label }}
            <span v-if="required" class="text-red-600">
                *
            </span>
        </label>
        <div class="relative mt-1">
            <div
                class="overflow-hidden transition duration-300 border border-gray-300 dark:border-gray-700 rounded-md shadow-sm dark:focus-within:border-primary-400 focus-within:border-primary-500 dark:focus-within:ring-primary-400 focus-within:ring-primary-500 focus-within:ring-1"
                :class="{
                    'ring-1 ring-red-500 focus-within:ring-red-500 text-red-500': !!errorMessage,
                    'focus-within:border-red-500 border-red-500': !!errorMessage,
                    'pl-10': preIcon,
                    'pr-10': postIcon
                }"
            >
                <div v-if="preIcon" class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                    <component :is="preIcon" class="w-5 h-5" :class="{ 'text-red-500': !!errorMessage }" />
                </div>
                <textarea
                    :id="uuid"
                    :name="name"
                    :type="type"
                    :value="inputValue"
                    :autocomplete="type"
                    :required="required"
                    :data-test="dataTest"
                    :placeholder="placeholder"
                    rows="5"
                    class="block w-full text-sm text-gray-700 dark:text-gray-300 dark:bg-gray-700 border-0 resize-y focus:ring-0"
                    :class="errorMessage ? 'placeholder-red-500/50' : 'placeholder:text-gray-400'"
                    v-bind="inputProps"
                    @input="handleChange"
                    @blur="handleBlur"
                />
                <div v-if="footer" class="py-1.5" aria-hidden="true">
                    <div class="py-px">
                        <div class="h-8" />
                    </div>
                </div>
            </div>
            <div
                v-if="footer"
                class="absolute inset-x-0 bottom-0 flex justify-between items-center py-1.5 pl-3 pr-2 text-sm border-t border-gray-200"
            >
                <slot name="footer" />
            </div>
        </div>
        <div v-if="postIcon" class="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
            <component :is="postIcon" class="w-5 h-5" :class="{ 'text-red-500': !!errorMessage }" />
        </div>
        <div v-if="hint && !errorMessage" class="m-1 text-sm text-gray-500">
            {{ hint }}
        </div>
        <div v-if="!!errorMessage" class="m-1 text-sm text-right text-red-500 error_message">
            {{ errorMessage }}
        </div>
    </div>
</template>

<script lang="ts">
import { useField } from "vee-validate";
import { defineComponent, TextareaHTMLAttributes } from "vue";

import { getRandomId } from "@/utils/helpers";

export default defineComponent({
    name: "AiTextArea",
    props: {
        required: {
            type: Boolean,
            default: false
        },
        footer: {
            type: Boolean,
            default: false
        },
        error: {
            type: String,
            default: undefined
        },
        preIcon: {
            type: Function,
            required: false,
            default: undefined
        },
        postIcon: {
            type: Function,
            required: false,
            default: undefined
        },
        type: {
            type: String,
            default: "text"
        },
        value: {
            type: String,
            default: ""
        },
        name: {
            type: String,
            required: true
        },
        label: {
            type: String,
            required: true
        },
        placeholder: {
            type: String,
            default: ""
        },
        hint: {
            type: String,
            default: ""
        },
        dataTest: {
            type: String,
            default: ""
        },
        inputProps: {
            type: Object as () => TextareaHTMLAttributes,
            required: false,
            default: undefined
        },
        initialValue: {
            type: String,
            required: false,
            default: ""
        }
    },
    setup(props) {
        const {
            value: inputValue,
            errorMessage,
            handleBlur,
            handleChange,
            meta
        } = useField(props.name, undefined, { initialValue: props.initialValue });

        return {
            handleChange,
            handleBlur,
            errorMessage,
            inputValue,
            meta,
            uuid: getRandomId()
        };
    }
});
</script>
