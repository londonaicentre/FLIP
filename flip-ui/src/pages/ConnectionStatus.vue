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

﻿<route lang="yaml">
    name: Connection Status
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <Transition name="fade" mode="out-in">
            <AiLoader v-if="!flStatus" class="py-8" />
            <div v-else class="w-full p-4 xl:overflow-y-auto">
                <AiCard class="w-full space-y-2">
                    <div class="relative p-4 overflow-hidden transition">
                        <div
                            class="relative"
                        >
                            <icon-ph-plugs-connected-duotone
                                v-if="healthyPlatform"
                                class="w-16 h-16 mb-8 transition text-green-600/70"
                            />
                            <icon-ph-plugs-duotone
                                v-else
                                class="w-16 h-16 mb-8 transition text-red-900/70 dark:text-red-400"
                            />
                            <h3 class="mt-2 text-3xl font-semibold font-heading">
                                The platform is <span
                                    class="font-black underline uppercase transition decoration-4 decoration-solid underline-offset-8"
                                    :class="[healthyPlatform ? 'decoration-green-600/70' : 'decoration-red-900/70 dark:decoration-red-400']"
                                >
                                    {{ healthyPlatform ? 'Healthy' : 'Unhealthy' }}
                                </span>
                            </h3>
                            <p class="max-w-2xl my-6 text-gray-500">
                                Each NET represents an environment where a model can train. No training requests can be
                                sent to a Trust if they are OFFLINE.<br>
                                We currently have <span class="font-bold">{{ flStatus?.length }} nets</span> connected.
                            </p>
                        </div>
                    </div>
                    <div class="relative p-4 space-y-2 bg-gray-200 dark:bg-gray-700 xl:columns-2 gap-x-2">
                        <div
                            v-for="net in flStatus"
                            :key="net.name"
                            class="relative p-2"
                        >
                            <div v-if="offlineClients(net.clients)" class="absolute inset-0.5 dark:inset-2 bg-gradient-to-bl from-red-500 dark:from-red-500 dark:to-red-500 to-red-800 blur-md opacity-25 dark:opacity-80" />
                            <div
                                class="relative w-full overflow-hidden border border-gray-300 rounded-lg shadow bg-gray-50 dark:bg-gray-800 dark:border-gray-700"
                            >
                                <div class="px-4 py-2 bg-white border-b border-gray-200 dark:border-gray-700 dark:bg-gray-900">
                                    <div class="flex flex-wrap items-center justify-between -mt-2 -ml-4 sm:flex-nowrap">
                                        <div class="mt-2 ml-4">
                                            <h3 class="text-lg font-medium leading-6 text-gray-900 uppercase dark:text-gray-300">
                                                {{ net.name }}
                                            </h3>
                                        </div>
                                        <div class="flex-shrink-0 mt-2 ml-4">
                                            <AiButton small @click="setAndShowDetails(net.name)">
                                                View Detailed Response
                                            </AiButton>
                                        </div>
                                    </div>
                                </div>
                                <ul
                                    role="list"
                                    class="h-full border-t border-gray-200 divide-y divide-gray-200 dark:border-gray-700 dark:divide-gray-700"
                                >
                                    <li
                                        v-for="(client, idx) in net.clients"
                                        :key="client.name"
                                        :data-test="`project-list-item-${idx}`"
                                    >
                                        <span
                                            class="block px-4 py-3 hover:bg-gray-100 dark:hover:bg-gray-900 sm:px-6 group"
                                            data-test="view-project-btn"
                                        >
                                            <span class="flex flex-row items-center gap-4 ">
                                                <icon-ph-check-circle-duotone
                                                    v-if="client.online"
                                                    class="w-6 h-6 transition transform rounded-full shrink-0 text-green-600/70 dark:text-green-400 group-hover:scale-110"
                                                />
                                                <icon-ph-x-circle-duotone
                                                    v-else
                                                    class="w-6 h-6 transition transform rounded-full shrink-0 text-red-600/70 dark:text-red-400 group-hover:scale-110"
                                                />
                                                <span class="w-full">
                                                    <span class="flex items-center gap-2">
                                                        <span class="flex flex-col w-full shrink">
                                                            <span
                                                                class="flex-wrap pr-4 line-clamp-3"
                                                            >
                                                                <p
                                                                    class="text-sm font-bold text-primary-600 dark:text-gray-400 shrink line-clamp-1"
                                                                    data-test="project-name"
                                                                >
                                                                    {{ client.name }}
                                                                </p>
                                                            </span>
                                                        </span>
                                                    </span>
                                                </span>
                                            </span>
                                        </span>
                                    </li>
                                    <li v-if="!net.clients" class="h-full">
                                        <div
                                            class="flex flex-col items-center justify-center h-full px-4 py-4"
                                        >
                                            <icon-ph-archive-duotone
                                                class="w-16 h-16 text-primary-500"
                                            />
                                            There are no clients in {{ net.name }}
                                        </div>
                                    </li>
                                </ul>
                            </div>
                        </div>
                    </div>
                </AiCard>
            </div>
        </Transition>
    </div>

    <AiCommand :open="showDetails" @close="hideDetails">
        <Transition name="fade" mode="out-in">
            <div v-if="detailsError" class="p-10">
                <AiAlert
                    variant="error"
                    text="We've been unable to load the detailed response for this net. Please check back later."
                />
            </div>
            <div v-else-if="!!details">
                <pre v-highlightjs><code class="json">{{ details }}</code></pre>
            </div>
            <div v-else class="h-40">
                <AiLoader inverted />
            </div>
        </Transition>
    </AiCommand>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, ref } from "vue";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiCommand from "@/components/AiCommand/AiCommand.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { getFLStatus, getNetDetailedStatus, IFLStatusClients } from "@/services/fl-service";

const showDetails = ref(false);
const details = ref<string | undefined>(undefined);
const detailsError = ref<boolean>(false);

const { data: flStatus } = useSWRV(
    "fl/status",
    getFLStatus,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 10_000
    }
);

const hideDetails = () => {
    showDetails.value = false;
    setTimeout(() => {
        details.value = undefined;
        detailsError.value = false;

    }, 500);
};
const offlineClients = (clients: IFLStatusClients[]) => {
    return clients.some(c => !c.online);
};

const setAndShowDetails = async (netName: string) => {
    showDetails.value = true;

    try {
        const detailedResponse = await getNetDetailedStatus(`/fl/${netName}/status`);
        details.value = JSON.stringify(detailedResponse, null, 2);
    }
    catch {
        detailsError.value = true;
    }

};

const healthyPlatform = computed(() => flStatus?.value?.every(a => a.clients.every(b => b.online)));

</script>
