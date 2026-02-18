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
    <div class="h-full gap-4 space-y-4 columns-1 lg:columns-2">
        <div class="relative p-4 bg-white border border-black rounded grow border-opacity-10 break-inside-avoid h-72">
            <div class="flex flex-col h-full">
                <AiSkeleton class="!w-36 h-8 mt-2" />

                <div class="flex flex-row items-end flex-grow gap-4">
                    <AiSkeleton class="h-16 mt-2" />
                    <AiSkeleton class="h-40 mt-2" />
                    <AiSkeleton class="mt-2 h-44" />
                    <AiSkeleton class="h-8 mt-2" />
                    <AiSkeleton class="h-16 mt-2" />
                    <AiSkeleton class="h-40 mt-2" />
                    <AiSkeleton class="h-8 mt-2" />
                    <AiSkeleton class="mt-2 h-44" />
                    <AiSkeleton class="h-40 mt-2" />
                    <AiSkeleton class="mt-2 h-44" />
                    <AiSkeleton class="h-8 mt-2" />
                    <AiSkeleton class="h-40 mt-2" />
                    <AiSkeleton class="h-16 mt-2" />
                </div>
            </div>
        </div>
    </div>
</template>

<script lang="ts" setup>
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
</script>
