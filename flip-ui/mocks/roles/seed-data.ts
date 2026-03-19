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




import { IRole, IRoleResponse } from "@/services/role-service";

export const roleAdmin: IRole = {
    id: "1470b37c-7c0d-4351-b6e8-0dde9798b0e0",
    rolename: "Admin",
    roledescription: "An administrator."
};

export const roleResearcher: IRole = {
    id: "1d30db72-d59c-499c-a2e4-3039bae61ce6",
    rolename: "Researcher",
    roledescription: "A researcher."
};

export const allRoles: IRoleResponse = {
    roles: [roleAdmin, roleResearcher]
};
