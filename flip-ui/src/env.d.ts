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

interface Window {
    Cypress: Cypress;
    pinia: Pinia;
    AWS_BASE_URL: string;
    // Which identity provider the hub runs (FLIP#919): "cognito" (default when
    // unset/empty) or "keycloak". The AWS_* values feed the Cognito provider,
    // the KEYCLOAK_* values the Keycloak one; the unused set is emitted empty.
    AUTH_BACKEND: string;
    AWS_USER_POOL_ID: string;
    AWS_CLIENT_ID: string;
    AWS_REGION: string;
    KEYCLOAK_URL: string;
    KEYCLOAK_REALM: string;
    KEYCLOAK_CLIENT_ID: string;
    BLACKLISTED_MODEL_FILES: string;
    RELEASE_VERSION: string;
}

declare module "*.vue" {
    import { DefineComponent } from "vue";
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-empty-object-type
    const component: DefineComponent<{}, {}, any>;
    export default component;
}

declare module "notiwind";
declare module "~icons/*";
