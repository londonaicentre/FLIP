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

﻿<template>
    <Listbox v-model="currentlySelected">
        <ListboxButton
            class="relative w-full py-2 pl-3 pr-10 mt-2 text-left bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-700 rounded-md cursor-default focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400 focus:ring-1"
            :class="[
                !!errorMessage &&
                    'ring-1 ring-red-500 focus:ring-red-500 text-red-500 dark:text-red-400 dark:focus:ring-red-400',
                !!errorMessage &&
                    'focus:border-red-500 border-red-500 dark:focus:border-red-400',
            ]"
        >
            <span
                class="block truncate"
                :class="!!!currentlySelected && 'text-gray-500'"
                data-test="chip-select"
            >
                {{ currentlySelected?.description ?? defaultText }}
            </span>
            <span
                class="absolute inset-y-0 right-0 flex items-center pr-2 pointer-events-none"
            >
                <icon-mdi-chevron-down class="w-5 h-5 text-gray-400" />
            </span>
        </ListboxButton>
        <ListboxOptions>
            <transition
                enter-active-class="transition duration-100 ease-out"
                enter-from-class="transform scale-95 opacity-0"
                enter-to-class="transform scale-100 opacity-100"
                leave-active-class="transition duration-75 ease-in"
                leave-from-class="transform scale-100 opacity-100"
                leave-to-class="transform scale-95 opacity-0"
            >
                <PopoverPanel
                    class="fixed z-10 py-2 origin-top-left bg-white dark:bg-gray-900 dark:ring-white/20 rounded-md shadow-2xl w-60 ring-1 ring-black ring-opacity-5 focus:outline-none"
                >
                    <ListboxOption
                        v-for="option in options"
                        v-slot="{ active }"
                        :key="option.id"
                        data-test="chip-select-option"
                        :value="option"
                    >
                        <li
                            class="relative px-4 py-2 pl-10 select-none transition"
                            :class="[
                                (selectedOptionsInclude(option)
                                    || active)
                                    && 'text-primary-500 bg-primary-100 dark:bg-gray-800 dark:text-primary-200'
                            ]"
                        >
                            <span>{{ option.description }}</span>
                            <span
                                class="absolute inset-y-0 left-0 flex items-center pl-3"
                            >
                                <icon-mdi-check
                                    v-if="selectedOptionsInclude(option)"
                                />
                            </span>
                        </li>
                    </ListboxOption>
                </PopoverPanel>
            </transition>
        </ListboxOptions>
    </Listbox>
    <div v-if="!!errorMessage" class="mt-1 text-sm text-red-500 dark:text-red-400 error_message">
        {{ errorMessage }}
    </div>

    <div class="flex flex-wrap">
        <AiButton
            v-for="(field, idx) in selectedOptions"
            :key="field.key"
            class="mt-2 mr-2"
            @click="removeOption(idx)"
        >
            {{ getOptionName(field) }}
            <icon-mdi-close class="ml-2" />
        </AiButton>
    </div>
</template>

<script setup lang="ts">
import { Listbox,
    ListboxButton,
    ListboxOption,
    ListboxOptions } from "@headlessui/vue";
import { FieldEntry } from "vee-validate";
import { DeepReadonly, ref, watch } from "vue";

import { IOption } from "@/components/AiSelect/interfaces";

import AiButton from "../AiButton/AiButton.vue";

interface IAiChipSelectProperties {
    errorMessage: string,
    defaultText: string,
    options: IOption[],
    selectedOptions: readonly DeepReadonly<FieldEntry>[],
}

const props = withDefaults(
    defineProps<IAiChipSelectProperties>(), {
        errorMessage: undefined,
        defaultText: "Please make a selection"
    }
);

const emit = defineEmits(["push", "remove", "validate"]);

const currentlySelected = ref<IOption>();

watch(currentlySelected, async (current) => {
    if (current) {
        if (!props.selectedOptions.some((field) => (field.value as IOption).id === current.id)) {
            emit("push", current);
        }
        emit("validate");
    }
});

const selectedOptionsInclude = (option: IOption): boolean => {
    return props.selectedOptions.some((o) => (o.value as IOption).id === option.id);
};

const removeOption = (id: number): void => {
    const fieldValue = props.selectedOptions[id].value as IOption;
    if (fieldValue.id === currentlySelected?.value?.id) {
        currentlySelected.value = undefined;
    }

    emit("remove", id);
    emit("validate");
};

const getOptionName = (field: DeepReadonly<FieldEntry>): string => {
    return (field.value as IOption).description;
};
</script>
