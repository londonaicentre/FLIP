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




import { validProject, validUsers } from "../../common";

describe("create project", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjectsEmpty" })
            .as("getProjectsEmpty");
        cy.intercept("GET", `/projects/${validProject.id}`, { fixture: "project/getProjectWithQuery" });
        cy.visit("/projects");
        cy.getBySel("add-project-btn").click();
    });

    it("successfully creates a new project when name and description is provided", () => {
        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");
        cy.getBySel("create-project-btn").click();
        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": []
        });
        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });

    it("displays an error message if the API call fails", () => {
        cy.intercept("POST", "/projects", { statusCode: 500 });

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");
        cy.getBySel("create-project-btn").click();

        cy.contains("There was a problem creating this project.").should("be.visible");
        cy.url().should("include", "/projects");
    });

    it("displays validation when name and description are not provided", () => {
        cy.getBySel("create-project-btn").click();

        cy.contains("A project name is required and can't be left blank.").should("be.visible");
        cy.url().should("include", "/projects");
    });

    it("displays validation messages when name and description are provided but are too long", () => {
        cy.getBySel("project-name")
            .type("Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Aenean commodo ligula eget dolor. Aenean m");
        cy.contains("Project name must not be longer than 75 characters.")
            .should("be.visible");

        cy.getBySel("project-description")
            .type(
                `Lorem ipsum dolor sit amet, consectetuer adipiscing elit.
                Aenean commodo ligula eget dolor. Aenean massa.
                Cum sociis natoque penatibus et magnis dis parturient montes, nascetur ridiculus mus.
                Donec quam felis, ultricies nec, pellentesque eu, pretium quis, sem.
                Nulla consequat massa quis enim. Donec.`,
                { delay: 0 }
            );
        cy.contains("Project description must not be longer than 250 characters.")
            .should("be.visible");
    });

    it("successfully validates the email when adding a user", () => {
        cy.intercept("GET", `/users/${validUsers[0].email}`, {
            statusCode: 200,
            body: {
                "id": "12345",
                "email": `${validUsers[0].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-0").contains(validUsers[0].email).should("be.visible");
        cy.url().should("include", "/projects");
    });

    it("does not add a disabled user to the project", () => {
        cy.intercept("GET", `/users/${validUsers[0].email}`, {
            statusCode: 200,
            body: {
                "id": "12345",
                "email": `${validUsers[0].email}`,
                "isDisabled": true
            }
        }).as("validateUser");

        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.contains(`${validUsers[0].email} is disabled`).should("be.visible");

        cy.getBySel("create-project-btn").click();
        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": []
        });

        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });

    it("attempts to add a user that does not exist, and when creating the project there are no users added", () => {
        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.intercept("GET", `/users/${validUsers[0].email}`, { statusCode: 404 }).as("validateUser");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.contains(`${validUsers[0].email} cannot be found`).should("be.visible");

        cy.getBySel("create-project-btn").click();
        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": []
        });

        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });

    it("attempts to add the same user twice, and when creating the project there is only one ID passed in", () => {
        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.intercept("GET", `/users/${validUsers[0].email}`, {
            statusCode: 200,
            body: {
                "id": "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
                "email": `${validUsers[0].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();

        cy.contains(`${validUsers[0].email} has already been added to the list`).should("be.visible");

        cy.getBySel("create-project-btn").click();
        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": [
                "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3"
            ]
        });

        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });

    it("successfully creates a new project when name, description, and users are added", () => {
        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");

        cy.intercept("GET", `/users/${validUsers[0].email}`, {
            statusCode: 200,
            body: {
                "id": "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
                "email": `${validUsers[0].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-0").contains(validUsers[0].email).should("be.visible");

        cy.intercept("GET", `/users/${validUsers[1].email}`, {
            statusCode: 200,
            body: {
                "id": "2635f591-1430-4d20-86e2-c0ee88c0a0c5",
                "email": `${validUsers[1].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").clear().type(validUsers[1].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-1").contains(validUsers[1].email).should("be.visible");

        cy.intercept("GET", `/users/${validUsers[2].email}`, {
            statusCode: 200,
            body: {
                "id": "9057b483-d483-47a1-af3b-72ca23893caa",
                "email": `${validUsers[2].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").clear().type(validUsers[2].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-2").contains(validUsers[2].email).should("be.visible");

        cy.getBySel("create-project-btn").click();

        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": [
                "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
                "2635f591-1430-4d20-86e2-c0ee88c0a0c5",
                "9057b483-d483-47a1-af3b-72ca23893caa"
            ]
        });

        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });

    it("successfully creates a new project when name, description, and users are added and removed", () => {
        cy.intercept("POST", "/projects", {
            statusCode: 200,
            body: { "id": validProject.id }
        }).as("createProject");

        cy.getBySel("project-name").type("Test Project Name");
        cy.getBySel("project-description").type("Test Project Description");

        cy.intercept("GET", `/users/${validUsers[0].email}`, {
            statusCode: 200,
            body: {
                "id": "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
                "email": `${validUsers[0].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        // add users
        cy.getBySel("add-user-project-input").type(validUsers[0].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-0").contains(validUsers[0].email).should("be.visible");

        cy.intercept("GET", `/users/${validUsers[1].email}`, {
            statusCode: 200,
            body: {
                "id": "2635f591-1430-4d20-86e2-c0ee88c0a0c5",
                "email": `${validUsers[1].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").clear().type(validUsers[1].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-1").contains(validUsers[1].email).should("be.visible");

        cy.intercept("GET", `/users/${validUsers[2].email}`, {
            statusCode: 200,
            body: {
                "id": "9057b483-d483-47a1-af3b-72ca23893caa",
                "email": `${validUsers[2].email}`,
                "isDisabled": false
            }
        }).as("validateUser");

        cy.getBySel("add-user-project-input").clear().type(validUsers[2].email);
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@validateUser");

        cy.getBySel("added-user-2").contains(validUsers[2].email).should("be.visible");

        // remove user
        cy.getBySel("remove-user-1-project-btn").click();
        cy.wait("@createProject");

        cy.get("@createProject").its("request.body").should("deep.equal", {
            "name": "Test Project Name",
            "description": "Test Project Description",
            "users": [
                "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
                "9057b483-d483-47a1-af3b-72ca23893caa"
            ]
        });

        cy.contains("Project created successfully").should("be.visible");
        cy.url().should("include", `/project/${validProject.id}`);
    });
});
