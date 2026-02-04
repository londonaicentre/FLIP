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

import { FileUploadStatus } from "@/interfaces/model/types";
import { IPaginatedResponse } from "@/services/api";
import { ILog, IModel, IModelDashboard, IModelMetricData } from "@/services/model-service";

const modelId = v4();

export const exampleLogs: ILog[] = [
    {
        "id": "3818fd33-d876-487e-872d-4382ab8d0794",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.637Z",
        "success": true,
        "trustName": "trust2",
        "log": "sending the training request..."
    },
    {
        "id": "db796fa7-333d-4825-9d07-707dba2b78ad",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.636Z",
        "success": true,
        "trustName": "trust2",
        "log": "returned status: alive"
    },
    {
        "id": "577b2273-57f8-4a2f-a6ba-4aaac5e2fda1",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.630Z",
        "success": true,
        "trustName": "trust1",
        "log": "sending the training request..."
    },
    {
        "id": "c163a3b6-cb6c-41be-883f-c050b466cc23",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.629Z",
        "success": true,
        "trustName": "trust1",
        "log": "returned status: alive"
    },
    {
        "id": "27f178ef-f6b4-439c-9fc4-be64f7caaf87",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.620Z",
        "success": true,
        "trustName": "trust2",
        "log": "checking fl trust status..."
    },
    {
        "id": "b75d170c-58d7-45c5-85df-4368333a2c53",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.615Z",
        "success": true,
        "trustName": "trust1",
        "log": "checking fl trust status..."
    },
    {
        "id": "b67db376-c062-43a2-ae94-9e623f7cc902",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.601Z",
        "success": true,
        "trustName": null,
        "log": "current model shared with all fl trusts"
    },
    {
        "id": "3b223eca-7382-4af1-950d-4dd691fff77e",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.580Z",
        "success": true,
        "trustName": "trust1",
        "log": "returned status: model received"
    },
    {
        "id": "d091defb-12d1-4ab1-867f-e2e66d48fc91",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.579Z",
        "success": true,
        "trustName": "trust1",
        "log": "answer received"
    },
    {
        "id": "0f8f6010-20d4-4d19-a2ef-2e51f4c337c0",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:45:58.508Z",
        "success": true,
        "trustName": "trust2",
        "log": "returned status: model received"
    },
    {
        "id": "ba2feac0-7112-40ab-b088-c61b26b3b14c",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:13.055Z",
        "success": true,
        "trustName": null,
        "log": "upload completed"
    },
    {
        "id": "6565469e-924b-4a6e-8cf1-dfcddf3bc228",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:13.055Z",
        "success": true,
        "trustName": null,
        "log": "fl hub terminated"
    },
    {
        "id": "6614671f-09a5-414f-a536-bbd5ee8d449a",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:11.376Z",
        "success": true,
        "trustName": null,
        "log": "uploading zip file..."
    },
    {
        "id": "305d588b-cc6e-4c74-a317-ffa0468f4454",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.655Z",
        "success": true,
        "trustName": null,
        "log": "federated process completed across all trusts"
    },
    {
        "id": "93c93cc3-6ef2-4ac9-ad9c-bf20c7f2ced7",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.655Z",
        "success": true,
        "trustName": null,
        "log": "zipping the final model and the reports..."
    },
    {
        "id": "069f7311-8d35-4967-b50d-e53c52054737",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.640Z",
        "success": true,
        "trustName": "trust2",
        "log": "received the trust status"
    },
    {
        "id": "1013f15d-2d2e-44db-9b98-c77406e7e0aa",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.640Z",
        "success": true,
        "trustName": "trust2",
        "log": "returned status: stopping"
    },
    {
        "id": "db1bdd0b-34ef-4ad7-8970-c816464c1d18",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.639Z",
        "success": true,
        "trustName": "trust1",
        "log": "received the trust status"
    },
    {
        "id": "d19f8303-ab40-48a4-9bff-933dc7dec7cf",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.639Z",
        "success": true,
        "trustName": "trust1",
        "log": "returned status: stopping"
    },
    {
        "id": "b0b2df25-209b-4983-9f0a-cb7e06492cd0",
        "modelId": "6e35c21b-a6f6-4fba-8228-8d260becb212",
        "logDate": "2021-11-10T16:47:09.636Z",
        "success": true,
        "trustName": "trust2",
        "log": "sending the stop message..."
    }
];

export const mockModelDashboard: IModelDashboard = {
    modelName: "Testing Model",
    modelDescription: "Worst case scenario, if we make our screen smaller we should see everything we need to check all is OK.",
    modelId,
    projectId: v4(),
    status: "INITIATED",
    query: {
        name: "My Cohort Query",
        query: "SELECT * FROM EARTH",
        results: [
            {
                Data: {
                    TotalCount: 3245,
                    Age: { Mean: 47 },
                    ClientVisit: {
                        Emergency: 783,
                        Inpatient: 1432,
                        MissingData: 7
                    },
                    Gender: {
                        Female: 567,
                        Male: 497,
                        MissingData: 3
                    }
                },
                TrustName: "trust1"
            },
            {
                Data: {
                    TotalCount: 3245,
                    Age: { Mean: 47 },
                    ClientVisit: {
                        Emergency: 783,
                        Inpatient: 1432,
                        MissingData: 7
                    },
                    Gender: {
                        Female: 567,
                        Male: 497,
                        MissingData: 3
                    }
                },
                TrustName: "trust2"
            }
        ]
    },
    files: [
        {
            id: v4(),
            name: "trainer.py",
            size: 1048576,
            status: FileUploadStatus.COMPLETED
        },
        {
            id: v4(),
            name: "validator.py",
            size: 8048576,
            status: FileUploadStatus.COMPLETED
        }
    ]
};

export const modelsDataPage1: IModel[] = [
    {
        id: "458a164e-1acd-11ec-9621-0242ac130002",
        name: "Model #1: Project 1",
        description: `This is some pretty heft model description.
        If it goes onto multiple lines it will actually cut off...
        This is some pretty heft model description.
        If it goes onto multiple lines it will actually cut off...
        This is some pretty heft model description.`,
        projectId: "458a10b8-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "019085cf-d277-4cd0-be16-20a52b12cdee",
        name: "Model #1: Project 2",
        projectId: "458a10b8-1acd-11ec-9621-0242ac130002",
        description: "🚀 Almost the best",
        ownerId: "test_username"
    },
    {
        id: "65cb28e7-278b-4207-ac31-b8e7da313ff5",
        name: "Model #1: Project 3",
        description: "The best model 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "da1b1940-4eb6-4090-b260-4ce6eb5abf77",
        name: "Model #1: Project 4",
        description: "The best model pt2 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "9e6793a4-586f-49a4-8a0e-0e31fa1ac10d",
        name: "Model #1: Project 5",
        description: "The best model pt3 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "2201fb8c-3b6a-4c37-aceb-a45cc6c89078",
        name: "Model #1: Project 6",
        description: "The best model pt4 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "76c1b813-0069-4a4c-815c-4221424fddf6",
        name: "Model #1: Project 7",
        description: "The best model pt5 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "c6de5186-8298-4eae-b2b0-b059b37f068b",
        name: "Model #1: Project 8",
        description: "The best model pt6 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "11fd34c1-fa7d-4ffb-ba85-3c32d18236e8",
        name: "Model #1: Project 9",
        description: "The best model pt7 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "25f76207-6d6c-45aa-9cb8-c8fe37c2827c",
        name: "Model #1: Project 10",
        description: "The best model pt8 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "f4537815-6188-4b5b-8624-1a933b31b9a8",
        name: "Model #1: Project 11",
        description: "The best model pt9 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "0070e409-a904-49af-a7e2-23e62c78e70b",
        name: "Model #1: Project 12",
        description: "The best model pt10 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "7f8fecd0-e36b-4a71-962b-0a4df0164ee4",
        name: "Model #1: Project 13",
        description: "The best model pt 11 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "4180e711-a726-4829-bc68-eb7932002b51",
        name: "Model #1: Project 14",
        description: "The best model pt12 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "bf9f38a1-f8c5-4dc0-9b7e-3cbca51bd57f",
        name: "Model #1: Project 15",
        description: "The best model pt13 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "b2779aff-b230-4456-882a-cddaed42fcbc",
        name: "Model #1: Project 16",
        description: "The best model pt14 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "6f37cc09-002d-4cf4-ba05-d02e87d29aec",
        name: "Model #1: Project 17",
        description: "The best model pt15 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "7eca783e-680b-40b7-8735-10ed3bc9b31c",
        name: "Model #1: Project 18",
        description: "The best model pt16 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "5e466e18-f0f7-4716-9e93-e45ce46c3bf6",
        name: "Model #1: Project 19",
        description: "The best model pt17 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    },
    {
        id: "be503b50-4ed6-4c4a-a805-9a7666370cde",
        name: "Model #1: Project 20",
        description: "The best model pt... lost count 🏆",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    }
];

export const modelsDataPage2: IModel[] = [
    {
        id: "42fe4fe6-d2b1-4445-abb9-cee9d9047052",
        name: "Model #1: Project 21",
        description: "The 21st model so it falls onto another page",
        projectId: "458a12f2-1acd-11ec-9621-0242ac130002",
        ownerId: "test_username"
    }
];

export const emptyPaginatedModelData: IPaginatedResponse<IModel> = {
    page: 1,
    pageSize: 20,
    totalPages: 1,
    totalRecords: 0,
    data: []
};

export const paginatedModelData1: IPaginatedResponse<IModel> = {
    page: 1,
    pageSize: 20,
    totalPages: 2,
    totalRecords: 21,
    data: modelsDataPage1
};

export const paginatedModelData2: IPaginatedResponse<IModel> = {
    page: 2,
    pageSize: 20,
    totalPages: 2,
    totalRecords: 21,
    data: modelsDataPage2
};

export const latestModelData1: IPaginatedResponse<IModel> = {
    page: 1,
    pageSize: 5,
    totalPages: 1,
    totalRecords: 5,
    data: modelsDataPage1.slice(0, 5)
};

export const ModelMetrics: IModelMetricData[] = [
    {
        "yLabel": "LOSS_FUNCTION",
        "xLabel": "Global Round",
        "metrics": [
            {
                "data": [
                    {
                        "xValue": 1,
                        "yValue": 32.1
                    },
                    {
                        "xValue": 2,
                        "yValue": 17.76
                    },
                    {
                        "xValue": 3,
                        "yValue": 5.9
                    },
                    {
                        "xValue": 4,
                        "yValue": 2.7
                    },
                    {
                        "xValue": 5,
                        "yValue": 17.7
                    },
                    {
                        "xValue": 6,
                        "yValue": 25.1
                    },
                    {
                        "xValue": 7,
                        "yValue": 33.1
                    }
                ],
                "seriesLabel": "UCLH"
            },
            {
                "data": [
                    {
                        "xValue": 1,
                        "yValue": 42.5
                    },
                    {
                        "xValue": 2,
                        "yValue": 24.2
                    },
                    {
                        "xValue": 3,
                        "yValue": 12.5
                    },
                    {
                        "xValue": 4,
                        "yValue": 6.9
                    },
                    {
                        "xValue": 5,
                        "yValue": 5.7
                    },
                    {
                        "xValue": 6,
                        "yValue": 2.1
                    },
                    {
                        "xValue": 7,
                        "yValue":
                            0.1
                    }
                ],
                "seriesLabel": "KCH"
            }
        ]
    },
    {
        "yLabel": "AVERAGE_SCORE",
        "xLabel": "Global Round",
        "metrics": [
            {
                "data": [
                    {
                        "xValue": 1,
                        "yValue": 2.1
                    },
                    {
                        "xValue": 2,
                        "yValue": 3.76
                    },
                    {
                        "xValue": 3,
                        "yValue": 2.9
                    },
                    {
                        "xValue": 4,
                        "yValue": 6.7
                    }
                ],
                "seriesLabel": "UCLH"
            },
            {
                "data": [
                    {
                        "xValue": 1,
                        "yValue": 2.5
                    },
                    {
                        "xValue": 2,
                        "yValue": 4.2
                    },
                    {
                        "xValue": 3,
                        "yValue": 2.5
                    },
                    {
                        "xValue": 4,
                        "yValue": 6.9
                    }
                ],
                "seriesLabel": "KCH"
            }
        ]
    }
];
