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
    <router-view />
    <AiSnackbar />
</template>

<script lang="ts" setup>
import { useDark } from "@vueuse/core";
import useSWRV from "swrv";
import { watch } from "vue";

import AiSnackbar from "@/components/AiSnackbar/AiSnackbar.vue";

import { getHealth } from "./services/health-service";
import { getSiteDetails } from "./services/site-service";
import { getTrusts } from "./services/trust-service";
import { useHealthcheckStore } from "./store/healthcheck";
import { useSiteDetailsStore } from "./store/siteDetailsStore";
import { useTrustStore } from "./store/trusts";

const healthStore = useHealthcheckStore();
const trustStore = useTrustStore();
const detailsStore = useSiteDetailsStore();
useDark();

const { data: health } = useSWRV(
    "/trust/health",
    getHealth,
    {
        refreshInterval: 5_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    });

const { data: trusts } = useSWRV(
    "/trust",
    getTrusts,
    {
        refreshInterval: 5_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    });

const { data: details } = useSWRV(
    "/site/details",
    getSiteDetails,
    {
        refreshInterval: 30_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    });

watch(health, (health) => {
    if(health) {
        healthStore.setHealth(health);
    }
}, { immediate: true });

watch(trusts, (trusts) => {
    if (trusts) {
        trustStore.setTrusts(trusts);
    }
}, { immediate: true });

watch(details, (details) => {
    if (details) {
        detailsStore.setSiteDetails(details);
    }
}, { immediate: true });
</script>
