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

import { object, string } from "yup";

// Validation rules for the "Add trust" form. Extracted from the component so the
// rules can be unit-tested directly (the vee-validate form round-trip is awkward
// to drive in jsdom). `code` is required: names are admin-chosen, non-unique, and
// may contain arbitrary characters, so the short code is the stable handle the
// hub and deploy tooling key on.
export const addTrustSchema = object().shape({
    name: string()
        .required("Trust name is required")
        .max(120, "Trust name must be 120 characters or fewer"),
    code: string()
        .required("Short code is required")
        .matches(/^[A-Za-z0-9_-]+$/, "Use letters, digits, underscore, or hyphen only")
        .max(16, "Short code must be 16 characters or fewer"),
    region: string().max(60, "Region must be 60 characters or fewer")
});
