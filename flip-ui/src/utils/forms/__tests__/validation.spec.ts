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

import { describe, expect, test } from "vitest";

import { emailValidation, passwordValidation } from "../validation";

describe("passwordValidation", () => {
    // "" pins the custom required message (yup's default would regress the forms'
    // UX); "Aa1!" passes every character-class matcher, so only min(8) fires.
    test.each([
        ["", "A password is required"],
        ["Aa1!", "Your password must be at least 8 characters"],
        ["Password!", "Your password must contain a number"],
        ["password1!", "Your password must contain an uppercase character"],
        ["Password1", "Your password must contain a special character"]
    ])("rejects %j with \"%s\"", async (input, message) => {
        await expect(passwordValidation.validate(input)).rejects.toThrow(message);
    });

    test("accepts a fully-valid password", async () => {
        await expect(
            passwordValidation.validate("Password1!")
        ).resolves.toBe("Password1!");
    });
});

describe("emailValidation", () => {
    test.each([
        ["", "An email address is required"],
        ["not-an-email", "Please enter a valid email address"]
    ])("rejects %j with \"%s\"", async (input, message) => {
        await expect(emailValidation.validate(input)).rejects.toThrow(message);
    });

    test("accepts a well-formed address", async () => {
        await expect(emailValidation.validate("u@e.com")).resolves.toBe(
            "u@e.com"
        );
    });
});
