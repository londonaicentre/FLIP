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




import { v4 } from "uuid";

import { IPaginatedResponse } from "@/services/api";
import { IImagingProjectStatus, IProject, IProjectTrust } from "@/services/project-service";

const approvedTrusts: IProjectTrust[] = [
    {
        name: "University College London Hospitals",
        id: "SOMEIDFORUCLH",
        approved: true
    },
    {
        name: "Guy's and St Thomas'",
        id: "SOMEIDFORKINGS",
        approved: true
    }
];

const trustsToStage: IProjectTrust[] = [
    {
        name: "Kings College Hospital",
        id: "SOMEIDFORKCH",
        approved: false
    },
    {
        name: "University College London Hospitals",
        id: "SOMEIDFORUCLH",
        approved: false
    },
    {
        name: "Guy's and St Thomas'",
        id: "SOMEIDFORKINGS",
        approved: false
    },
    {
        name: "Leeds Teaching Hospitals",
        id: "SOMEIDFORLEEDS",
        approved: false
    }
];

export const projectDataPage1: IProject[] = [
    {
        id: "a93416bf-3be0-4451-9a01-f32e77c87189",
        name: "Stroke Test Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Stroke Test Query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        users: [],
        status: "APPROVED"
    },
    {
        id: "4090e6d9-d602-42dc-a507-ed86b503eea3",
        name: "Heart Disease Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Stroke Pathway Query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        status: "UNSTAGED"
    },
    {
        id: "2ac3af2c-e743-4124-ab14-f3a8d0fe012f",
        name: "Stroke Pathway Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Stroke Pathway Query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        users: [],
        status: "APPROVED"
    },
    {
        id: "09303ed9-f2c8-427e-91d8-38ddb06f2ac4",
        name: "A test project with a fairly long description. A description that should be long enough to at least alter the view of the table. It might not be long enough to span all three lines but it should give some indication of how we can handle a longer description. Worst case scenario, if we make our screen smaller we should see everything we need to check all is OK.",
        description: "A test project with a fairly long description. A description that should be long enough to at least alter the view of the table. It might not be long enough to span all three lines but it should give some indication of how we can handle a longer description. Worst case scenario, if we make our screen smaller we should see everything we need to check all is OK.",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        users: [],
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Stroke Test Query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        status: "APPROVED"
    },
    {
        id: "58920505-6026-4ad5-acff-4150a248ccdb",
        name: "Severe Covid Deterioration",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Severe Covid Deterioration query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        users: [
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            }, {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            },
            {
                email: "user1@email.com",
                id: v4(),
                isDisabled: false
            }
        ],
        status: "APPROVED"
    },
    {
        id: "73ede9b1-ce72-421a-9e17-e735dd4ac0dd",
        name: "Lung Biopsy Surgery",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Lung Biopsy Surgery Query",
            query: "SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table SELECT Column FROM Table ",
            totalCohort: 32567,
            trustsQueried: 2
        },
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "b065cce0-d4cf-44e6-a19f-fe909c3ef4a4",
        name: "Another Heart Disease Project",
        description: "A test project with a fairly long description. A description that should be long enough to at least alter the view of the table. It might not be long enough to span all three lines but it should give some indication of how we can handle a longer description. Worst case scenario, if we make our screen smaller we should see everything we need to check all is OK.",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Query For Another Heart Disease Project",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        users: [],
        status: "APPROVED"
    },
    {
        id: "0de8010c-4e92-46cf-8aea-f93901a6f7dc",
        name: "Lung Biopsy Surgery",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "93c1890f-cfcc-4e42-a151-85101dda3f05",
        name: "Heart Disease Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        users: [],
        query: {
            id: "58920505-6027-4ad5-acff-4150a248ccdb",
            name: "A Stroke Test Query",
            query: "Some query > 3000",
            totalCohort: 32567,
            trustsQueried: 2
        },
        status: "STAGED"
    },
    {
        id: "af47948f-df09-41e4-9570-8a872d12617a",
        name: "Severe Covid Deterioration",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "ecbd9c36-cc46-4ac9-8919-97fbea4e19c3",
        name: "Stroke Pathway Project 1a",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "fa60928a-4077-4532-a00f-708a74ed55cf",
        name: "Stroke Pathway Project 1b",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "9e18ca1a-4fe5-4a9f-9d97-5e6b0791d267",
        name: "Another Heart Disease Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "f0aa6c2e-932e-443c-9945-2223c67b44a0",
        name: "And Another Heart Disease Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "db6246ed-2bff-4248-ade4-1f36a787f315",
        name: "Heart Disease Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "de1641ea-3035-4f27-8f5f-148f5f255fb8",
        name: "Stroke Test Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "d44e72c8-bd9e-42f0-ada3-cc656e2cf5f0",
        name: "Stroke Test Project",
        description: "A test project",
        ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "c396f974-65a1-4af0-9edc-4f24e50ebb2a",
        name: "Another Stroke Test Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "e7956bcd-3e87-4a5b-b687-b668a91c7bac",
        name: "A Different Stroke Test Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "ce628569-f03d-4d5e-8622-f82cd96e6ca3",
        name: "Heart Disease Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: trustsToStage,
        users: [],
        status: "UNSTAGED"
    }
];

export const projectDataPage2: IProject[] = [
    {
        id: "a93416bf-3be0-4451-9b01-f32e77c87189",
        name: "Lung Biopsy Surgery",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        users: [],
        status: "UNSTAGED"
    },
    {
        id: "a93416bf-3be0-4451-9c01-f32e77c87189",
        name: "Another Heart Disease Project",
        description: "A test project",
        ownerId: "82d151f8-1a84-4e19-a1e2-ff949f588856",
        ownerEmail: "test.user@example.com",
        creationtimestamp: "2022-02-07T14:10:56.687Z",
        approvedTrusts: approvedTrusts,
        users: [],
        status: "UNSTAGED"
    }
];

export const paginatedProjectData1: IPaginatedResponse<IProject> = {
    page: 1,
    pageSize: 20,
    totalPages: 2,
    totalRecords: 22,
    data: projectDataPage1
};

export const paginatedProjectData2: IPaginatedResponse<IProject> = {
    page: 2,
    pageSize: 20,
    totalPages: 2,
    totalRecords: 22,
    data: projectDataPage2
};

export const singleProject: IProject = {
    id: "458a12f2-1acd-11ec-9621-0242ac130002",
    name: "Another Heart Disease Project",
    description: "A test project which has a decent sized description just to see how it would actually look on a page if someone could be bothered to actually type this much information into the little box when creating a project.",
    ownerId: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
    ownerEmail: "test.user@example.com",
    creationtimestamp: "2022-02-07T14:10:56.687Z",
    approvedTrusts: approvedTrusts,
    users: [],
    status: "UNSTAGED"
};

export const ownerProjects: IPaginatedResponse<IProject> = {
    page: 1,
    pageSize: 20,
    totalPages: 1,
    totalRecords: 2,
    data: projectDataPage2
};

export const imagingProjectStatus: IImagingProjectStatus[] = [
    {
        trustId: "7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2",
        trustName: "KCH",
        projectCreationCompleted: true,
        importStatus: {
            successful: 41,
            failed: 11,
            processing: 22,
            queued: 29,
            queueFailed: 51
        },
        reimportCount: 2
    },
    {
        trustId: "5d512a2b-747e-4b1f-ad9d-f65fdb3c6585",
        trustName: "GSTT",
        projectCreationCompleted: false
    },
    {
        trustId: "df8f0069-ad2c-44a7-b082-10e84d453b24",
        trustName: "UCLH",
        projectCreationCompleted: true,
        importStatus: {
            successful: 12,
            failed: 3,
            processing: 2,
            queued: 212,
            queueFailed: 23
        },
        reimportCount: 5
    }
];
