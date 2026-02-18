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




import { IModel } from "@/services/model-service";
import { IProjectUser } from "@/services/user-service";

export const validProject = {
    id: "6fcbdd40-3675-45c9-899e-1a005e5245ba",
    name: "Stroke Test Project",
    description: "A test project",
    ownerid: "c16dab4d-ab43-4bea-9be5-a045c6d684ff",
    owneremail: "test.user@example.com",
    creationtimestamp: "2022-02-07T14:10:56.687Z",
    approvedtrusts: [
        {
            name: "Kings College Hospital",
            id: "SOMEIDFORKCH",
            requested: true,
            approved: false
        },
        {
            name: "University College London Hospitals",
            id: "SOMEIDFORUCLH",
            requested: true,
            approved: true
        },
        {
            name: "Guy's and St Thomas'",
            id: "SOMEIDFORKINGS",
            requested: true,
            approved: true
        }
    ],
    approved: true,
    dataRequested: false,
    users: [
        {
            id: "03a3a210-cd5a-405e-a838-07a16aefc806",
            email: "joe@bloggs.com",
            isDisabled: false
        },
        {
            id: "04a3a210-cd5a-405e-a838-07a16aefc806",
            email: "test.tester@sloggs.com",
            isDisabled: false
        }
    ]
};

export const validProjectWithQuery = {
    ...validProject,
    query: {
        id: "58920505-6027-4ad5-acff-4150a248ccdb",
        name: "Some test query",
        query: "Some Query > 3000",
        totalCohort: 32567,
        trustsQueried: 2
    }
};

export const validUsers: IProjectUser[] = [
    {
        id: "ad1fbfc0-e6dc-40e1-9a6c-0019cf490fa3",
        email: "test1@exmple.com"
    },
    {
        id: "2635f591-1430-4d20-86e2-c0ee88c0a0c5",
        email: "test2@exmple.com"
    },
    {
        id: "9057b483-d483-47a1-af3b-72ca23893caa",
        email: "test3@exmple.com"
    }
];

export const validModel: IModel = {
    id: "6292d9ec-e821-4e4a-814e-3a315a4cb95e",
    name: "Test model",
    description: "Test model description",
    projectId: validProject.id,
    ownerId: validUsers[0].id
};
