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




import { _http } from "./api";

export interface IRole {
    id: string,
    rolename: string,
    roledescription: string,
}

/** The trust-scoped role (FLIP#1258): Researcher access plus one trust's project decisions. */
export const TRUST_ADMIN_ROLE_NAME = "Trust Admin";

export interface IRoleResponse {
    roles: IRole[]
}

export async function getRoles(url: string): Promise<IRoleResponse> {
    const response = await _http.get<IRoleResponse>(url);

    return response.data;
}
