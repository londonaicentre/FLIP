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




import { IPaginatedResponse } from "@/services/api";
import { IUser } from "@/services/user-service";

import { roleAdmin, roleResearcher } from "../roles/seed-data";

export const users: IUser[] = [
    {
        id: "1e195cde-8210-40a4-91d4-8c8582a29298",
        email: "john.smith@example.com",
        roles: [roleResearcher],
        isDisabled: false
    },
    {
        id: "87f892d2-908b-4a2d-8597-c34b65952620",
        email: "this.is.a.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.really.long.email.but.still.valid@example.com",
        roles: [roleResearcher],
        isDisabled: false
    },
    {
        id: "1a1af391-d0d6-469c-886b-7a6b6d31ab65",
        email: "disabled.user@example.com",
        roles: [roleResearcher],
        isDisabled: true
    },
    {
        id: "5c052df5-4886-450a-9c0c-649c5f8c4fc6",
        email: "mr.admin@example.com",
        roles: [roleAdmin],
        isDisabled: false
    }
];

export const paginatedUsers: IPaginatedResponse<IUser> = {
    page: 4,
    pageSize: 20,
    totalPages: 6,
    totalRecords: 3,
    data: users
};
