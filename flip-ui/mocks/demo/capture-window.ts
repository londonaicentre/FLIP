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
 * The exhibit's capture window as literals, for the one place in the MAIN
 * bundle that needs it: src/demo/DemoBanner.vue (mounted in App.vue behind the
 * VITE_DEMO literal). It must not import the registry — that pulls every
 * fixture JSON into a non-demo build, which scripts/assert-no-demo-artefacts.mjs
 * refuses. mocks/__tests__/registry.spec.ts fails when these drift from what
 * the registry computes, and scripts/generate-demo-window-js.sh greps
 * LATEST_CAPTURE_DATE out of this file for window.RELEASE_VERSION.
 */
export const PROJECT_COUNT: number = 5;
export const EARLIEST_CAPTURE_DATE: string = "2026-07-17";
export const LATEST_CAPTURE_DATE: string = "2026-11-02";
