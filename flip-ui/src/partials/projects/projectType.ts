// Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { IProject } from "@/services/project-service";

// Project-type vocabulary (FLIP#1071). Both kinds are named: imaging projects use OMOP data too,
// so "Structured data" alone would misdescribe them.
export const IMAGING_LABEL = "Imaging + OMOP";
export const OMOP_ONLY_LABEL = "OMOP only";

// The hub's `projectType=` wire vocabulary (`ProjectType` in flip-api), and the list's Type
// segmented filter on top of it — "all" is UI-only and sends no query parameter.
export type ProjectTypeParam = "imaging" | "omop_only";
export type ProjectTypeFilter = "all" | ProjectTypeParam;
// DOM ids (data-test hooks, option keys) for the same two kinds.
export type ProjectTypeId = "imaging" | "omop-only";

// `has_imaging` is absent on a hub predating the flag, which means imaging (the old behaviour), as
// does a project that has not loaded yet. Every "absent means imaging" decision goes through here.
export const projectHasImaging = (project?: Pick<IProject, "has_imaging"> | null): boolean =>
    project?.has_imaging ?? true;

export const projectTypeId = (hasImaging: boolean): ProjectTypeId => (hasImaging ? "imaging" : "omop-only");
