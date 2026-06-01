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
 * Returns true when the user-typed value matches the expected confirmation
 * word (case-insensitive), or when no confirmation is required at all.
 *
 * `typed` is undefined on the first yup validation pass (before the user
 * touches the input). Coalescing to "" inside the function keeps callers
 * — and the schema's test() callback — from having to guard against that.
 * `expected` is required and typed as `string`, so callers can't pass
 * undefined; if they ever do, the early-return on "" still applies.
 */
export function confirmsTypedValue(
    typed: unknown,
    expected: string
): boolean {
    if (expected === "") {
        return true;
    }
    const typedStr = (typed ?? "").toString();

    return typedStr.toUpperCase() === expected.toUpperCase();
}
