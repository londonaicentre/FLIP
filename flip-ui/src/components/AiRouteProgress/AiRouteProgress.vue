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

<!--
    Thin top-of-page loading bar shown while a lazy route chunk is being
    fetched. State lives in @/router/progress and is driven by the router
    guards; this component only renders it. Decorative/non-interactive, so it
    is hidden from the accessibility tree.
-->
<template>
    <div
        v-show="routeProgress.visible"
        class="fixed top-0 left-0 z-[100] h-0.5 bg-primary-500 dark:bg-primary-300 transition-[width] duration-200 ease-out"
        :style="{ width: `${routeProgress.width}%` }"
        role="presentation"
        aria-hidden="true"
        data-test="route-progress"
    />
</template>

<script setup lang="ts">
import { routeProgress } from "@/router/progress";
</script>
