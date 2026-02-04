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

import { fetchAuthSession } from "aws-amplify/auth";
import { Hub } from "aws-amplify/utils";
import { StoreGeneric } from "pinia";
import { NavigationGuardNext, RouteLocationNormalized } from "vue-router";

import router, { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";

import { Snackbar } from "./snackbar";

/**
 * By default all routes are guarded and authentication is required.
 * This serves as an allowed list of routes which will bypass the auth check.
 * @type {string[]}
 */
const unguardedRoutes: string[] = [
    "/auth/login",
    "/auth/new-password",
    "/auth/change-password",
    "/auth/access-request",
    "/privacy-policy",
    "/terms-of-service"
];

/**
 * Checks auth status for the requested route.
 * Bypasses check for any routes that are included in the unguarded routes list.
 * On any error getting the authenticated user:
 * 1. clear down any existing auth state
 * 2. clear localStorage (aws-amplify stores tokens here)
 * 3. Redirect to login page
 * 4. Give the user some indication as to what's going on
 */
export const authCheck = async (
    to: RouteLocationNormalized,
    _from: RouteLocationNormalized,
    next: NavigationGuardNext
): Promise<void> => {
    const errorStore = useErrorStore();
    const auth = useAuthStore();

    try {
        errorStore.clearError();

        if (unguardedRoutes.includes(to.path)) {
            return next();
        }

        if (process.env.VITE_LOCAL === "true") {
            return next();
        }

        // Check if user has a valid session
        try {
            await fetchAuthSession();
        } catch {
            // No valid session, redirect to login
            auth.user = null;
            auth.signInStep = null;
            return next("/auth/login");
        }

        // If we are in new-password challenge
        if (auth.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') {
            return next('/auth/new-password');
        }

        // Load user info if needed
        if (!auth.user) {
            await auth.fetchInfo();
        }

        return next();

    } catch (error) {
        auth.$reset();
        localStorage.clear();
        routeChange.gotoLogin();
        Snackbar.error({
            title: "You've been signed out",
            text: "Please log in again to confirm your identity."
        });
    }
};

export const isUserUnconfirmedCheck = async (
    authStore: StoreGeneric
): Promise<boolean> => {
    // Keep the same logic as before, but using v6 state:
    // true unless the user is exactly in "new password required".
    return (
        authStore.user === null ||
        authStore.confirmedUser ||
        authStore.signInStep !== "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED"
    );
};

export const apiGateway = "CentralHubAPIGateway";

const devMode = process.env.NODE_ENV === "development";

export const authConfig = {
    Auth: {
        Cognito: {
            region: process.env.VITE_AWS_REGION || 'eu-west-2',
            userPoolId: process.env.VITE_AWS_USER_POOL_ID,
            userPoolClientId: process.env.VITE_AWS_CLIENT_ID,
            clientSecret: process.env.VITE_AWS_CLIENT_SECRET,
            authenticationFlowType: 'USER_PASSWORD_AUTH',
            loginWith: {}
        }
    }
};

let tokenRefreshTimeout = 0;

const listener = (data: { payload: { event: string } }) => {
    const store = useAuthStore();

    switch (data.payload.event) {
        case "tokenRefresh_failure":
            if (router.currentRoute.value.fullPath === "/auth/login") {
                break;
            }

            if (tokenRefreshTimeout) {
                clearTimeout(tokenRefreshTimeout);
                tokenRefreshTimeout = 0;
            }

            tokenRefreshTimeout = window.setTimeout(() => {
                Snackbar.error({
                    title: "You've been signed out",
                    text: "Your session has expired. Please log in again."
                }, 60_000);

                routeChange.gotoLogin();
                store.$reset();
            }, 100);
            break;
    }
};

Hub.listen("auth", listener);
