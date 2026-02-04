/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




import { IRole } from "@/services/role-service";

import { _http, IPaginatedResponse } from "./api";

interface IUserPermissions {
    permissions: string[];
}

export interface IUser {
    id: string;
    email: string;
    roles: IRole[];
    isDisabled: boolean;
}

export interface IRegisterUserDto {
    email: string,
    roles: string[]
}

export interface IUserDisabledStateDto {
    disabled: boolean
}

export interface IProjectUser {
    id: string;
    email: string;
    isDisabled: boolean;
}

export interface IAccessRequest {
    email: string;
    fullName: string;
    reasonForAccess: string;
}

export async function getUserPermissions(id: string): Promise<IUserPermissions> {

    try {
        const response = await _http.get<IUserPermissions>(`/users/${id}/permissions`);

        return response.data;
    } catch {
        return { permissions: [] };
    }
}

export async function getUsers(url: string): Promise<IPaginatedResponse<IUser>> {
    const response = await _http.get<IPaginatedResponse<IUser>>(url);

    return response.data;
}

export async function updateUserRoles(userId: string, roleIds: string[]): Promise<string[]> {
    const response = await _http.post<string[]>(`/users/${userId}/roles`, { roles: roleIds });

    return response.data;
}

export async function registerUser(user: IRegisterUserDto): Promise<IRegisterUserDto> {
    const response = await _http.post<IRegisterUserDto>("/step/users", user);

    return response.data;
}

export async function updateUserDisabledState(userId: string, state: IUserDisabledStateDto):
    Promise<IUserDisabledStateDto> {
    const response = await _http.put<IUserDisabledStateDto>(`/users/${userId}`, state);

    return response.data;
}

export async function validateUser(email: string): Promise<IProjectUser> {
    const response = await _http.get<IProjectUser>(`/users/${email}`);

    return response.data;
}

export async function revokeToken(refreshToken: string): Promise<void> {
    await _http.put(`/users/revoke/${refreshToken}`);
}

export async function submitAccessRequest(requestBody: IAccessRequest): Promise<void> {
    await _http.post<string>("/users/access", requestBody, { headers: { Authorization: "" } });
}
