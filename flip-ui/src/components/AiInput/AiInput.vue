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
            v-if="label"
            :for="uuid"
            class="flex text-sm font-bold"
            :class="{ '!text-red-500 dark:!text-red-400': !!errorMessage, 'text-gray-700 dark:text-gray-400': !errorMessage}"
        >
            <span class="flex-grow">
                {{ label }}
                <span v-if="required" class="text-red-600">
                    *
                </span>
            </span>
            <slot name="labelRight" />
        </label>
        <div class="relative" :class="{'mt-1': label}">
            <div v-if="preIcon" class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                <component :is="preIcon" class="w-5 h-5" :class="!!errorMessage ? 'text-red-500 dark:!text-red-400' : 'text-gray-500 dark:text-gray-400'" />
            </div>
            <div class="flex flex-row items-center">
                <input
                    :id="uuid"
                    :name="name"
                    :type="type"
                    :value="inputValue"
                    :autocomplete="type"
                    :placeholder="placeholder"
                    :required="required"
                    :data-test="dataTest"
                    class="block w-full text-sm text-gray-700 dark:text-gray-300 dark:bg-gray-700 transition duration-300 border-gray-300 dark:border-gray-700 rounded-md shadow-sm focus:ring-1"
                    :class="[
                        !!errorMessage
                            ? 'ring-1 ring-red-500 focus:ring-red-500 text-red-500 focus:border-red-500 border-red-500 placeholder-red-500/50'
                            : 'placeholder:text-gray-400 focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400',
                        preIcon && 'pl-10',
                        postIcon && 'pr-10',

                    ]"
                    v-bind="inputProps"
                    @input="handleChangeEvent"
                    @blur="handleBlur"
                >
                <slot name="inputButton" />
            </div>
            <div v-if="postIcon" class="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
                <component :is="postIcon" class="w-5 h-5" :class="!!errorMessage ? 'text-red-500 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'" />
            </div>
        </div>
        <div v-if="hint && !errorMessage" class="m-1 text-sm text-gray-500 dark:text-gray-400">
            {{ hint }}
        </div>
        <div v-if="!!errorMessage" class="m-1 text-sm text-right text-red-500 dark:text-red-400 error_message">
            {{ errorMessage }}
        </div>
    </div>
</template>

<script lang="ts">
import { useField } from "vee-validate";
import { defineComponent, InputHTMLAttributes } from "vue";

import { getRandomId } from "@/utils/helpers";

export default defineComponent({
    name: "AiInput",
    props: {
        required: {
            type: Boolean,
            default: false
        },
        error: {
            type: String,
            default: undefined
        },
        preIcon: {
            type: Object,
            required: false,
            default: undefined
        },
        postIcon: {
            type: Object,
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
            required: false,
            default: undefined
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
            type: Object as () => InputHTMLAttributes,
            required: false,
            default: undefined
        },
        initialValue: {
            type: String,
            required: false,
            default: ""
        }
    },
    emits: [
        "change"
    ],
    setup(props) {
        const {
            value: inputValue,
            errorMessage,
            handleBlur,
            handleChange,
            meta
        } = useField(props.name, undefined, {
            initialValue: props.initialValue,
            validateOnMount: false
        });

        return {
            handleChange,
            handleBlur,
            errorMessage,
            inputValue,
            meta,
            uuid: getRandomId()
        };
    },
    methods: {
        handleChangeEvent(event: Event) {
            this.handleChange(event);
            this.$emit("change", event);
        }
    }
});
</script>
