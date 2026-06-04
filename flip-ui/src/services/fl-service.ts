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




import _ from "underscore";

import { _http } from "./api";

export interface IFLStatusClients {
    name: string;
    // The trust's short code (e.g. "GSTT"), shown as "name (code)" in the card.
    code?: string | null;
    online: boolean;
    status: string;
    // The FL kit slot backing this client (e.g. "Trust_1"). May differ from
    // `name` (the trust's display name) once a trust is registered under an
    // arbitrary name; surfaced so the card can show the underlying FL identity.
    fl_kit_slot?: string | null;
}
export interface IFLStatus {
    name: string;
    fl_backend?: "nvflare" | "flower";
    clients: IFLStatusClients[]
}


export async function getFLStatus(url: string): Promise<IFLStatus[]> {

    const response = await _http.get<IFLStatus[]>(url);

    return _.sortBy(response.data, "name") ?? [];
}

export async function getNetDetailedStatus(url: string): Promise<IFLStatus> {

    const response = await _http.get<IFLStatus>(url);

    return response.data;
}
