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




describe("Access request page", () => {
    const validInputs = {
        email: "valid@email.com",
        fullName: "Joe Bloggs",
        reasonForAccess: "This is a reason for access"
    };

    beforeEach(() => {
        cy.intercept("POST", "/users/access", cy.spy().as("submitAccessRequestSpy")).as("submitAccessRequest");
        cy.visit("/auth/access-request");
    });

    it("should be able to submit a valid form", () => {
        cy.getBySel("email-input")
            .should("be.visible")
            .clear()
            .type(validInputs.email);

        cy.getBySel("full-name-input")
            .should("be.visible")
            .clear()
            .type(validInputs.fullName);

        cy.getBySel("reason-for-access-textarea")
            .should("be.visible")
            .clear()
            .type(validInputs.reasonForAccess);

        cy.getBySel("submit-access-request-btn")
            .should("be.visible")
            .click();

        cy.wait("@submitAccessRequest");

        cy.get("@submitAccessRequest").its("request.body").should("deep.include", validInputs);

        cy.getBySel("snackbar")
            .contains("Success!")
            .should("be.visible")
            .its("length")
            .should("eq", 1)
            .getBySel("snackbar-text")
            .contains("Access request has been successfully submitted!")
            .should("be.visible");

        cy.url().should("include", "/auth/login");
    });

    it("should not be able to submit a form with no inputs", () => {
        cy.getBySel("email-input")
            .should("be.visible")
            .clear();

        cy.getBySel("full-name-input")
            .should("be.visible")
            .clear();

        cy.getBySel("reason-for-access-textarea")
            .should("be.visible")
            .clear();

        cy.getBySel("submit-access-request-btn")
            .should("be.visible")
            .click();

        cy.get("@submitAccessRequestSpy").should("not.have.been.called");

        cy.contains("Email address is required");
        cy.contains("Your full name is required");
        cy.contains("Reason for access is required");

        cy.url().should("include", "/auth/access-request");
    });

    it("should not be able to submit form with invalid email", () => {
        cy.getBySel("email-input")
            .should("be.visible")
            .clear()
            .type("invalid");

        cy.getBySel("full-name-input")
            .should("be.visible")
            .clear()
            .type(validInputs.fullName);

        cy.getBySel("reason-for-access-textarea")
            .should("be.visible")
            .clear()
            .type(validInputs.reasonForAccess);

        cy.getBySel("submit-access-request-btn")
            .should("be.visible")
            .click();

        cy.get("@submitAccessRequestSpy").should("not.have.been.called");

        cy.contains("Email address must be in a valid format");

        cy.url().should("include", "/auth/access-request");
    });

    it("should display an error snackbar if request fails to submit", () => {
        const errorBody = "{ error: \"Something went wrong\" }";
        cy.intercept("POST", "/users/access", {
            statusCode: 500,
            body: errorBody
        }).as("failedSubmitAccessRequest");

        cy.getBySel("email-input")
            .should("be.visible")
            .clear()
            .type(validInputs.email);

        cy.getBySel("full-name-input")
            .should("be.visible")
            .clear()
            .type(validInputs.fullName);

        cy.getBySel("reason-for-access-textarea")
            .should("be.visible")
            .clear()
            .type(validInputs.reasonForAccess);

        cy.getBySel("submit-access-request-btn")
            .should("be.visible")
            .click();

        cy.wait("@failedSubmitAccessRequest");

        cy.getBySel("snackbar")
            .contains("Error")
            .should("be.visible")
            .its("length")
            .should("eq", 1)
            .getBySel("snackbar-text")
            .contains("Failed to submit access request. Please try again later.")
            .should("be.visible");

        cy.url().should("include", "/auth/access-request");
    });

    it("should go back to the login page upon clicking 'Back to log in'", () => {
        cy.getBySel("back-to-login-btn")
            .should("be.visible")
            .click();

        cy.url().should("include", "/login");
    });
});
