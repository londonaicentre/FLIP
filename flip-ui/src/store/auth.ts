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

import {
    confirmResetPassword,
    confirmSignIn,
    fetchAuthSession,
    fetchUserAttributes,
    getCurrentUser,
    resetPassword,
    signIn,
    signOut
} from "aws-amplify/auth";

import { defineStore } from "pinia";

import { IChangePassword } from "@/interfaces/auth/interfaces";
import { routeChange } from "@/router";
import { getUserPermissions, revokeToken } from "@/services/user-service";

/**
 * Available User Permissions
 */
export type UserPermissions =
    | "CanManageUsers"
    | "CanManageProjects"
    | "CanApproveProjects"
    | "CanDeleteAnyProject"
    | "CanUnstageProjects"
    | "CanManageSiteBanner"
    | "CanManageDeployments"
    | "CanAccessAdminPanel";

type Attributes = {
    sub: string;
    email: string;
};

type AmplifyUser = {
  username: string;
  userId: string;
  attributes: Attributes;
  permissions: string[];
};

type UserCredentials = {
    username: string;
    password: string;
};

/**
 * Sign-in steps for the user.
 * - "DONE" means fully signed in.
 * - "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED" indicates the user needs to set a new password.
 * - Other strings can be used for additional steps (like MFA, etc).
 */
type SignInStep = "DONE" | "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED" | string;

type AuthState = {
  user: AmplifyUser | null;
  signInStep: SignInStep | null; // track v6 nextStep
};

const buildUserWithPermissions = async (
  base: Pick<AmplifyUser, "username" | "userId">
): Promise<AmplifyUser> => {
  const attributes = (await fetchUserAttributes()) as Attributes;
  const permsRes = await getUserPermissions(attributes.sub);

  return {
    ...base,
    attributes,
    permissions: permsRes.permissions ?? []
  };
};


export const useAuthStore = defineStore("auth", {
    state: (): AuthState => ({
        user: null,
        signInStep: null
    }),
    
    getters: {
        getUser: (state) => state.user,
        confirmedUser: (state) => state.signInStep === "DONE" && !!state.user
    },
    actions: {
        async fetchInfo() {
            // Get the currently signed-in user (no sign-in step here)
            const { username, userId } = await getCurrentUser();
            this.user = await buildUserWithPermissions({ username, userId });
            this.signInStep = "DONE";
        },

        async signIn(details: UserCredentials) {
            // Always clear stale user state at the start of login
            this.user = null;
            this.signInStep = null;

            const out = await signIn({
                username: details.username,
                password: details.password
            });

            // Extract next step
            const step = out.nextStep?.signInStep as SignInStep | undefined;

            if (step) {
                this.signInStep = step;
            }

            // NEW PASSWORD REQUIRED
            if (step === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
                // No session exists yet — do not try to fetch user info
                return;
            }

            if (out.isSignedIn) {
                // Fully signed in
                const { username, userId } = await getCurrentUser();
                this.user = await buildUserWithPermissions({ username, userId });
                this.signInStep = "DONE";
                return;
            }

            // Handle other steps your app might support (MFA, TOTP, etc) as needed.
        },

        async changePassword(newPassword: string) {
            await confirmSignIn({ challengeResponse: newPassword });

            // After confirming new password, user should be signed in
            const { username, userId } = await getCurrentUser();
            this.user = await buildUserWithPermissions({ username, userId });
            this.signInStep = "DONE";
        },

        async signOut() {
            try {
                const session = await fetchAuthSession();
                const refreshToken = session.tokens?.refreshToken?.toString();

                await signOut({ global: true });

                if (refreshToken) {
                    await revokeToken(refreshToken);
                }
            } catch (error) {
                console.error("Sign out error:", error);
            }

            this.$reset();
            routeChange.gotoLogin();
        },
        
        async resetPassword(email: string) {
            const response = await resetPassword({
                username: email,
                options: {
                clientMetadata: {
                    source: "web-app"
                }
                }
            });
            return response;
        },

        async updateForgottenPassword({ email, code, newPassword }: IChangePassword) {
            const response = await confirmResetPassword({
                username: email,
                confirmationCode: code,
                newPassword,
                options: {
                    clientMetadata: {
                        source: "web-app"
                    }
                }
            });
            return response;
        },

        hasPermissions(permissions: UserPermissions[]) {
            return permissions.every(perm => this.user?.permissions?.includes(perm));
        }
    }
});
