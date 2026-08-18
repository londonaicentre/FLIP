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

beforeEach(() => {

    cy.intercept("http://localhost:8080/**", { statusCode: 200 }).as("catchAllGlobal");

    // The catch-all answers 200 with an empty body, which `fetchJobTypes` rejects as unusable —
    // so every spec that opens a model page needs a real map here, not just the ones asserting on
    // required files. Specs that care about a particular job type override this locally.
    cy.intercept(
        "GET",
        "/model/job-types", {
        statusCode: 200,
        body: { standard: ["trainer.py", "config.json", "models.py"] }
    }
    ).as("jobTypesGlobal");

    cy.intercept(
        "POST",
        "https://cognito-idp.eu-west-2.amazonaws.com/", {
        statusCode: 200,
        fixture: "auth/cognitoAuth"
    }
    ).as("cognitoAuthGlobal");

    cy.intercept(
        "GET",
        "/trust/health", {
        statusCode: 200,
        fixture: "health/health"
    }
    ).as("healthCheckGlobal");

    cy.intercept(
        "GET",
        "/trust", {
        statusCode: 200,
        fixture: "trust/all"
    }
    ).as("trustGlobal");

    cy.intercept(
        "GET",
        "/users/*/permissions", {
        statusCode: 200,
        fixture: "user/getPermissions"
    }
    ).as("getPermissionsGlobal");

    cy.intercept(
        "GET",
        "/projects?pageNumber=1&pageSize=20", {
            statusCode: 200,
            fixture: "project/getProjects"
        }
    ).as("getProjectsGlobal");

    cy.intercept("/site/details", {
        statusCode: 200,
        fixture: "admin/bannerDisabled"
    }).as("siteDetailsGlobal");

    cy.intercept("/projects/*/image/status", {
        statusCode: 200,
        fixture: "project/imagingProjectStatus"
    }).as("imagingStatusGlobal");
});
