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

<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
    name: Forbidden
</route>

<template>
    <div class="flex flex-col items-center flex-1 p-8 space-y-6">
        <icon-ph-lock-duotone class="font-black w-36 h-36 text-primary-600 dark:text-primary-400" data-test="title" />

        <div class="items-center mt-8 text-center text-gray-600 dark:text-gray-500">
            <p class="text-2xl font-bold max-w-7xl">
                You don't have access to view this page
            </p>
            <p class="pt-4 text-lg">
                Please contact the service desk if you should be able to access this page.
            </p>
        </div>
        <AiButton large primary link="/projects" data-test="home-button">
            View Homepage
        </AiButton>
    </div>
</template>
