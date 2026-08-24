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
    Persistent (non-dismissible) label for the public Ark+ demo build. The
    demo renders as an ordinary signed-in session — this is the only visible
    signal to an anonymous visitor that they are looking at a read-only
    snapshot rather than a live platform. Unlike AiBanner (the site-wide
    announcement banner in MainLayout.vue), this carries no close control: it
    must stay on screen for the whole session, on every page, not just the
    pages MainLayout wraps — mounted once in App.vue, outside <router-view>.
-->

<template>
    <div
        class="w-full py-1.5 px-3 text-sm font-medium text-center text-white bg-amber-600 dark:bg-amber-700"
        data-test="demo-banner"
    >
        Read-only demo — snapshot of the Ark+ federated experiments, captured {{ capturedOn }}.
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { DEMO_CAPTURE_DATE } from "../../mocks/demo/ark-plus-register";

const capturedOn = computed(() => new Date(`${DEMO_CAPTURE_DATE}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC"
}));
</script>
