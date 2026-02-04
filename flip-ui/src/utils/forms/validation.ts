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




import { object, string } from "yup";

export const passwordValidation = string()
    .required()
    .matches(/[!&^#$*.[\]"{}()?=\-@%/\\,><':;|_~`+]/, "Your password must contain a special character")
    .matches(/(?=.*[A-Z])/, "Your password must contain an uppercase character")
    .matches(/(?=.*[a-z])/, "Your password must contain a lowercase character")
    .matches(/(\d)/, "Your password must contain a number")
    .min(8, "Your password must be at least 8 characters");

export const emailValidation = string()
    .required("An email address is required")
    .email("Please enter a valid email address");

export const projectSchema = object().shape({
    name: string()
        .trim()
        .max(75, "Project name must not be longer than 75 characters.")
        .required("A project name is required and can't be left blank."),
    description: string()
        .trim()
        .max(250, "Project description must not be longer than 250 characters.")
        .optional()
});

export const modelSchema = object().shape({
    name: string()
        .trim()
        .max(75, "Model name must not be longer than 75 characters.")
        .required("A model name is required and can't be left blank."),
    description: string()
        .trim()
        .max(250, "Model description must not be longer than 250 characters.")
        .optional()
});
