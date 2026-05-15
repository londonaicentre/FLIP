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




import { IRole } from "@/services/role-service";
import { Snackbar } from "@/utils/snackbar";

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
    } catch (e) {
        // Authorization is fail-closed (`hasPermissions` returns false on
        // an empty list), but a silent return would leave the user with
        // a half-broken UI and no diagnostic. 401 is already handled by
        // the response interceptor (sign-out + "Not Authorised"
        // snackbar), so a second snackbar here would just duplicate.
        // Surface only on 5xx / network / parse errors — exactly the
        // cases the user needs to know about.
        const status = (e as { response?: { status?: number } }).response?.status;
        if (status !== 401) {
            console.error("Failed to fetch user permissions:", e);
            Snackbar.error({
                title: "Couldn't load your permissions",
                text: "Some features may be unavailable. Please reload the page."
            });
        }

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
    await _http.put("/users/revoke", { refresh_token: refreshToken });
}

export async function resetUserMfa(userId: string): Promise<void> {
    await _http.post(`/users/${userId}/mfa/reset`);
}

export async function getMfaStatus(): Promise<{ enabled: boolean; required: boolean }> {
    const response = await _http.get<{ enabled: boolean; required: boolean }>("/users/me/mfa/status");

    return response.data;
}

export async function submitAccessRequest(requestBody: IAccessRequest): Promise<void> {
    await _http.post<string>("/users/access", requestBody, { headers: { Authorization: "" } });
}
