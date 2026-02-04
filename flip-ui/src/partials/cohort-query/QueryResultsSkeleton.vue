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
    <div class="flex items-center">
        <AiSkeleton class="w-56 h-14" />
        <AiSkeleton class="w-24 h-10 ml-4" />
    </div>
    <div class="mt-8 transition">
        <div class="flex items-start">
            <AiSkeleton class="h-8 w-80" />
        </div>
        <AiSkeleton class="w-full h-8 mt-2" />
        <AiSkeleton class="w-full h-8" />
        <AiSkeleton class="w-full h-8" />
    </div>
    <div class="grid items-center grid-cols-1 gap-8 mt-8 lg:grid-cols-2">
        <div>
            <div class="flex items-start">
                <AiSkeleton class="h-8 w-80" />
            </div>
            <AiSkeleton class="w-full h-8 mt-2" />
            <AiSkeleton class="w-full h-8" />
            <AiSkeleton class="w-full h-8" />
        </div>
        <div>
            <div class="flex items-start">
                <AiSkeleton class="h-8 w-80" />
            </div>
            <AiSkeleton class="w-full h-8 mt-2" />
            <AiSkeleton class="w-full h-8" />
            <AiSkeleton class="w-full h-8" />
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";

import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";

export default defineComponent({ components: { AiSkeleton } });
</script>