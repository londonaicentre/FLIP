/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */




/// <reference types="vite/client" />
/// <reference types="vite-plugin-pages/client" />
/// <reference types="vite-plugin-layouts/client" />
/// <reference types="vite-svg-loader" />
/// <reference types="aws-sdk" />

interface Window {
    Cypress: Cypress;
    pinia: Pinia;
    AWS_BASE_URL: string;
    AWS_USER_POOL_ID: string;
    AWS_CLIENT_ID: string;
    BLACKLISTED_MODEL_FILES: string;
    RELEASE_VERSION: string;
    MAX_REIMPORT_COUNT: number;
}

declare module "*.vue" {
    import { DefineComponent } from "vue";
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/ban-types
    const component: DefineComponent<{}, {}, any>;
    export default component;
}

declare module "notiwind";
declare module "~icons/*";
declare module "vue3-highlightjs";
