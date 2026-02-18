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
    <div
        class="fixed inset-0 z-10 flex items-start justify-end p-6 px-4 py-6 pointer-events-none"
    >
        <div class="w-full max-w-md p-2">
            <TransitionGroup
                enter-active-class="transition duration-300 ease-out"
                enter-from-class="transform scale-50 opacity-0"
                enter-to-class="transform scale-100 opacity-100"
                leave-active-class="transition duration-150 ease-in"
                leave-from-class="transform scale-100 opacity-100"
                leave-to-class="transform scale-50 opacity-0"
                move-class="transition duration-500"
            >
                <div
                    v-for="notification in sortedNotifications"
                    :key="notification.id"
                    class="flex w-full max-w-md p-2 mx-auto"
                    data-test="snackbar"
                >
                    <div class="flex items-center w-full max-w-md bg-white dark:bg-gray-900 dark:ring-white/20 rounded-lg shadow-lg dark:shadow-white/10 pointer-events-auto ring-1 ring-black ring-opacity-5">
                        <div class="flex-1 w-0 p-4">
                            <div class="flex items-start">
                                <div class="flex-shrink-0">
                                    <icon-ic-twotone-info v-if="notification.type === 'info'" class="w-5 h-5 text-blue-500 dark:text-blue-400" />
                                    <icon-ic-twotone-warning v-if="notification.type === 'warning'" class="w-5 h-5 text-yellow-600 dark:text-yellow-400" />
                                    <icon-ic-twotone-check-circle v-if="notification.type === 'success'" class="w-5 h-5 text-lightgreen-900 dark:text-green-400" />
                                    <icon-ic-twotone-cancel v-if="notification.type === 'error'" class="w-5 h-5 text-red-500 dark:text-red-400" />
                                </div>
                                <div class="flex-1 w-0 ml-3">
                                    <p class="text-sm font-bold text-gray-700 dark:text-gray-300">
                                        {{ notification.title }}
                                    </p>
                                    <p class="mt-1 text-sm text-gray-500 dark:text-gray-400 preserve-linebreaks" data-test="snackbar-text">
                                        {{ notification.text }}
                                    </p>
                                </div>
                            </div>
                        </div>
                        <div class="flex flex-col py-2 space-y-2 border-l border-gray-200 dark:border-gray-700">
                            <button
                                v-if="notification.actionText && !!notification.action"
                                class="flex items-center justify-center w-full px-4 py-1 text-sm font-bold rounded-none transition text-primary-500 dark:text-primary-300 hover:text-primary-400 dark:hover:text-primary-400 focus:outline-none"
                                data-test="snackbar-action-button"
                                @click="() => {
                                    notification.action?.();
                                    close(notification.id)
                                }"
                            >
                                {{ notification.actionText }}
                            </button>
                            <hr v-if="notification.actionText && notification.action" class="dark:border-gray-700">
                            <button
                                class="flex items-center justify-center w-full px-4 py-1 text-sm font-bold text-gray-500 transition rounded-none hover:text-gray-600 focus:outline-none dark:text-gray-400 dark:hover:text-gray-500"
                                data-test="snackbar-dismiss-button"
                                @click="() => {
                                    remove(notification.id)
                                }"
                            >
                                Dismiss
                            </button>
                        </div>
                    </div>
                </div>
            </TransitionGroup>
        </div>
    </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, Ref, ref } from "vue";

import { events } from "./events";
import { ISnackBarWithId } from "./notify";

const emit = defineEmits(["close"]);

onMounted( () =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    events.on("notify", (args: any) => add(args))
);

const notifications: Ref<ISnackBarWithId[]> = ref([]);
const maxNotifications = 10;

const sortedNotifications = computed(() => {

    const newNotifications = notifications.value;

    return newNotifications.reverse().slice(0, maxNotifications);
});

const add = ({ notification, timeout }: { notification: ISnackBarWithId, timeout: number }) =>
{
    const DEFAULT_TIMEOUT = 5_000;

    notifications.value.push(notification);

    setTimeout(() => {
        remove(notification.id);
    }, timeout || DEFAULT_TIMEOUT);
};

const remove = (id: number) => {
    notifications.value.splice(notifications.value.findIndex(n => n.id === id), 1);
};

const close = (id: number) => {
    emit("close");
    remove(id);
};

</script>

<style scoped>
.preserve-linebreaks {
  white-space: pre-line;
}
</style>
