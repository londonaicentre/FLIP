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

<route lang="yaml">
    name: Platform Updating
</route>

<template>
    <div class="flex flex-col items-center flex-1 p-8 space-y-6" data-test="deployment-mode-page">
        <icon-ph-download-duotone class="font-black w-36 h-36 text-primary-600" data-test="title" />

        <div class="items-center mt-8 text-center text-gray-600">
            <p class="text-2xl font-bold max-w-7xl">
                We're currently updating the platform
            </p>
            <p class="pt-4 text-lg">
                This page has been disabled whilst the platform is updated. This shouldn't take too long.
            </p>
            <p class="pt-4 text-lg">
                Please contact the service desk if you should be able to access this page.
            </p>
        </div>
    </div>
</template>
