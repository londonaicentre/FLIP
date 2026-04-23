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




import { Statistics, TrustsResults } from "@/interfaces/cohort-query/types";

import { _http } from "./api";

export interface ICohortStats {
    Status: string;
    AggregatedStats: {
        Stats: Statistics;
    }
    TrustsResults: TrustsResults[];
}

interface ITrustDetails {
    name: string;
    statusCode: number;
    message?: string;
}

export interface ICohortQueryResponse {
    trust: ITrustDetails[];
    queryId: string;
}

export interface ICohortQueryCreate {
    name: string;
    projectId: string;
    query: string;
}

interface IResultDataValue {
    value: string;
    count: number;
}

/**
 * Results for a Trust - Makes up chart series data
 */
interface IResultData {
    data: IResultDataValue[];
    trustName: string;
    trustId: string;
}

/**
 * Results aggregated by name
 */
export interface IResults {
    name: string;
    results: IResultData[]
}

/**
 * Get Cohort Query Results
 * @param {string} url - The URL of the OMOP API endpoint.
 * @returns The data returned from the OMOP API, or `null` while the hub is still waiting
 *          for trusts to post their results (HTTP 202).
 */
export async function getOMOPResults(url: string): Promise<IResults | null> {
    const response = await _http.get<IResults>(url, {
        // Accept 202 (pending) as a non-error response so SWRV keeps polling
        // on its refresh interval instead of logging an axios error in the console.
        validateStatus: (status) => (status >= 200 && status < 300) || status === 202,
    });

    if (response.status === 202) {
        return null;
    }

    return response.data;
}

export async function sendQuery(url: string, body: ICohortQueryCreate): Promise<ICohortQueryResponse> {
    const response = await _http.post<ICohortQueryResponse>(url, body);

    return response.data;
}
