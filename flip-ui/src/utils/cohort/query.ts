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




/*
 * There is deliberately no client-side SQL denylist here.
 *
 * This module used to export `containsForbiddenCommands`, a keyword regex that
 * duplicated one the hub also carried. It blocked every legitimate use of the
 * standard `SUBSTRING()` function while stopping nothing an attacker would do,
 * and being a third copy of one policy it was guaranteed to drift.
 *
 * The layering is now: this form validates only that a query was entered; the
 * hub runs a parser-based validity pre-check and answers synchronously before
 * fanning the query out, so malformed SQL still fails fast; and the trust's
 * data-access-api is the authority that decides what may actually run. See
 * `trust/data-access-api/README.md#cohort-query-validation`.
 */

/**
 * Restrict a list of trusts to those that participated in a cohort query.
 *
 * Returns the input unchanged when `queriedTrustIds` is undefined — that
 * signals the project (and therefore the trust set) hasn't loaded yet.
 * An empty array is a meaningful "no participants" answer and filters
 * everything out. Internally uses a Set for O(1) membership.
 */
export const filterByQueriedTrustIds = <T extends { id: string }>(
    trusts: T[],
    queriedTrustIds: string[] | undefined
): T[] => {
    if (queriedTrustIds === undefined) return trusts;
    const set = new Set(queriedTrustIds);

    return trusts.filter((t) => set.has(t.id));
};
