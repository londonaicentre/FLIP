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
    <!-- <div
        class="inline-block w-full text-left"
    > -->
    <Listbox v-model="selected" as="div">
        <div class="">
            <ListboxButton
                ref="popoverButton"
                class="relative w-full py-2 pl-3 pr-10 text-left bg-white border border-gray-300 rounded-md cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-opacity-75 focus-visible:ring-white focus-visible:ring-offset-primary-500 focus-visible:ring-offset-2 focus-visible:border-primary-500 sm:text-sm"
                data-test="select-component-btn"
            >
                <span class="block truncate" data-test="select-component-display-text">{{ selected }}</span>
                <span
                    class="absolute inset-y-0 right-0 flex items-center pr-2 pointer-events-none"
                >
                    <icon-mdi-chevron-down class="w-5 h-5 text-gray-400" />
                </span>
            </ListboxButton>
            <Portal>
                <div ref="popoverPanel">
                    <transition
                        leave-active-class="transition duration-100 ease-in"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                    >
                        <ListboxOptions
                            class="absolute w-56 py-1 mt-1 overflow-auto text-base bg-white rounded-md shadow-lg max-h-60 ring-1 ring-black ring-opacity-5 focus:outline-none sm:text-sm"
                            data-test="select-component-menu"
                        >
                            <ListboxOption
                                v-for="item in items"
                                v-slot="{ active, selected: selectedItem }"
                                :key="item.id"
                                :value="item.text"
                            >
                                <li
                                    :class="[
                                        selectedItem && 'text-primary-500 bg-primary-100',
                                        active && 'text-primary-500 bg-primary-100',
                                        'cursor-default select-none relative py-2 px-4 hover:cursor-pointer',
                                    ]"
                                >
                                    <span
                                        :class="[
                                            selectedItem ? 'font-bold' : 'font-normal text-gray-700',
                                            'block truncate',
                                        ]"
                                    >
                                        {{ item.text }}
                                    </span>
                                </li>
                            </ListboxOption>
                        </ListboxOptions>
                    </transition>
                </div>
            </Portal>
        </div>
    </Listbox>
</template>

<script lang="ts">
import { Listbox,
    ListboxButton,
    ListboxOption,
    ListboxOptions,
    Portal } from "@headlessui/vue";
import { defineComponent } from "vue";

import { usePopper } from "@/utils/popper";

export interface ISelectItem {
    text: string;
    id: number;
}

export default defineComponent({
    name: "AiSelect",
    components: {
        Listbox,
        ListboxButton,
        ListboxOptions,
        ListboxOption,
        Portal
    },
    props: {
        items: {
            type: Array as () => ISelectItem[],
            required: true
        },
        defaultText: {
            type: String,
            required: false,
            default: "Select item"
        }
    },
    setup() {
        const { trigger : popoverButton, popper : popoverPanel } = usePopper({
            placement: "top-start",
            strategy: "fixed"
        });

        return {
            popoverButton,
            popoverPanel
        };
    },
    data() {
        return {
            menuOpen: false,
            selected: this.defaultText
        };
    },
    methods: {
        openMenu() {
            this.menuOpen = !this.menuOpen;
        },
        closeMenu() {
            this.menuOpen = false;
        },
        selectedValue(itemName: string) {
            this.selected = itemName;
            this.menuOpen = false;
        }
    }
});
</script>

<style lang="css" scoped>
.select-component-menu {
    @apply absolute w-56 mt-3 origin-top-right bg-white divide-y divide-gray-100;
    @apply rounded-md shadow-sm ring-black ring-1 ring-opacity-5;
    @apply focus:outline-none;
}
</style>
