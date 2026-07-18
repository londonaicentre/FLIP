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

/**
 * Boot helpers for the public Ark+ demo (VITE_DEMO build). Offline API config
 * (window.AWS_BASE_URL = "/api", placeholder Cognito values) is supplied by the
 * generated public/js/window.js — a classic <script> that runs before any ES
 * module — and re-pinned in src/main.ts as belt-and-braces. This module only
 * exposes the demo flag and the read-only identity seeder.
 *
 * In any non-demo build `import.meta.env.VITE_DEMO` is inlined to a non-"true"
 * value by Vite and these branches are dead-code-eliminated.
 */

import { useAuthStore } from "@/store/auth";

export const IS_DEMO = import.meta.env.VITE_DEMO === "true";

/**
 * Pre-built zips of each recorded run's uploaded model files (including the
 * ~795 MB checkpoints), keyed by model id. Used by the "Download all" button
 * in demo builds — the in-browser mock cannot stream file bodies of that
 * size. Served through the production CloudFront distribution
 * (/ark_demo/assets/* behaviour → OAC-locked S3 origin, WAF in path), never
 * from a public S3 URL — see deploy/providers/AWS/cloudfront.tf
 * ("Public Ark+ demo download assets").
 */
const DEMO_ASSETS = "https://app.flip.aicentre.co.uk/ark_demo/assets";
export const DEMO_MODEL_FILES_ZIP_URLS: Record<string, string> = {
    // Fine-tuning run
    "24985ec3-3349-435b-afcd-f38972d8695d": `${DEMO_ASSETS}/model-24985ec3-3349-435b-afcd-f38972d8695d-files.zip`,
    // Evaluation: single-model baseline
    "db780699-61a9-4701-990f-8f43ac03f4ab": `${DEMO_ASSETS}/model-db780699-61a9-4701-990f-8f43ac03f4ab-files.zip`,
    // Evaluation: pretrained-vs-finetuned comparison
    "fcf8cb36-5685-4085-abc1-14bd25151566": `${DEMO_ASSETS}/model-fcf8cb36-5685-4085-abc1-14bd25151566-files.zip`
};

/**
 * Seed a read-only "viewer" identity so the app chrome renders a signed-in
 * researcher without any Cognito round-trip. Empty `permissions` makes
 * usePermissions().isViewer true, which hides every create/edit/stage/train
 * control across the project and model pages. Call after pinia is installed.
 */
export function seedDemoAuth(): void {
    if (!IS_DEMO) return;

    useAuthStore().user = {
        username: "flip-demo",
        userId: "demo-researcher",
        attributes: {
            sub: "demo-researcher",
            email: "demo@flip.local"
        },
        permissions: []
    };
}
