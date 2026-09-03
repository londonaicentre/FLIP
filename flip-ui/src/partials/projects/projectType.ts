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

// Project-type vocabulary (FLIP#1071, design handoff "Non-Imaging Project Page"). Both kinds are
// named — imaging projects use structured data too, so "Structured data" was rejected in review.
export const IMAGING_LABEL = "Imaging + OMOP";
export const OMOP_ONLY_LABEL = "OMOP only";

// The list's Type segmented filter; "all" sends no query parameter, the others map onto the
// hub's `projectType=` filter verbatim.
export type ProjectTypeFilter = "all" | "imaging" | "omop_only";

// `has_imaging` is absent on a hub predating the flag, which means imaging (the old behaviour).
export const projectHasImaging = (project: Pick<IProject, "has_imaging">): boolean => project.has_imaging ?? true;
