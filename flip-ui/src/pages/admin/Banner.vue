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

<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
    name: Site Banner
</route>

<template>
    <AiCard class="w-full space-y-2">
        <SiteBanner />
    </AiCard>
</template>

<script setup lang="ts">
import { onBeforeMount } from "vue";

import AiCard from "@/components/AiCard/AiCard.vue";
import SiteBanner from "@/partials/admin/banner/SiteBanner.vue";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { canAccessRoute } from "@/utils/route-validator";

const authStore = useAuthStore();

onBeforeMount(async () => {
    if (!(await canAccessRoute(authStore, ["CanManageSiteBanner"]))) {
        routeChange.notAllowed();
    }
});

</script>
