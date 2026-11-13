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

import useSWRV from "swrv";
import { computed, ComputedRef } from "vue";

import { getHubHealth, getTrustStatuses, IHubHealth, ITrustResponse } from "@/services/trust-service";

// One key and cadence for every page, so Connection Status and My Trust share one poll.
// SWRV handles 5s dedupe + 15s polling so the heartbeat column stays fresh
// without per-page setInterval bookkeeping.
export const useTrustStatuses = () => useSWRV<ITrustResponse[]>(
    "trust-connection-status",
    getTrustStatuses,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 15_000
    }
);

// The hub's own build (FLIP#1204). Changes only on a hub deploy, so a slow poll is plenty.
export const useHubVersion = (): ComputedRef<string | null> => {
    const { data: hubHealth } = useSWRV<IHubHealth>(
        "hub-health",
        getHubHealth,
        {
            dedupingInterval: 30_000,
            shouldRetryOnError: false,
            refreshInterval: 60_000
        }
    );

    return computed(() => hubHealth.value?.version ?? null);
};
