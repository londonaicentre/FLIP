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
 * A single step in an AiSteps lifecycle tracker (e.g. the model or project page).
 *
 * Lives here rather than in AiSteps.vue so non-presentation modules (such as
 * model-service) can build IStep[] without depending on a Vue component.
 */
export interface IStep {
    id: string;
    name: string;
    description?: string;
    scrollTo?: string;
    completed?: boolean;
    inProgress?: boolean;
    error?: boolean;
    stopped?: boolean;
    action?: () => void;
    date?: string | null;
}
