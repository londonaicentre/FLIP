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




import { createServer, Model, Registry, Response, Server } from "miragejs";
import { ModelDefinition } from "miragejs/-types";
import Schema from "miragejs/orm/schema";
import { v4 } from "uuid";

import { FileUploadStatus } from "@/interfaces/model/types";
import { IGenericResponse, IPaginatedResponse } from "@/services/api";
import { IModel } from "@/services/model-service";
import { IProject } from "@/services/project-service";
import { IUser } from "@/services/user-service";
import { ISiteDetails } from "@/store/siteDetailsStore";

import OMOPResults from "./cohort-query/omop-results.json";
import healthcheckResponse from "./data/healthcheck.json";
import { connectedNets, detailedNetResponse } from "./fl/status";
import {
    exampleLogs,
    latestModelData1,
    mockModelDashboard,
    ModelMetrics,
    modelsDataPage1,
    modelsDataPage2,
    paginatedModelData1,
    paginatedModelData2
} from "./models/seed-data";
import {
    imagingProjectStatus,
    paginatedProjectData1,
    paginatedProjectData2,
    projectDataPage1,
    projectDataPage2
} from "./projects/seed-data";
import { allRoles } from "./roles/seed-data";
import { siteDetails } from "./site/details";
import userPermissions from "./user/permissions.json";
import { paginatedUsers, users } from "./user/seed-data";

const modelsModel: ModelDefinition<IModel> = Model.extend({});
const projectsModel: ModelDefinition<IProject> = Model.extend({});
const usersModel: ModelDefinition<IUser> = Model.extend({});
const detailsModel: ModelDefinition<ISiteDetails> = Model.extend({});

// eslint-disable-next-line @typescript-eslint/ban-types
export type AppRegistry = Registry<{}, {}>;

type AppSchema = Schema<AppRegistry>


export const makeServer = ({ environment = "development" } = {}): Server<AppRegistry> => {

    const server = createServer({
        environment,
        models: {
            model: modelsModel,
            projects: projectsModel,
            users: usersModel,
            details: detailsModel
        },
        seeds(server) {
            server.db.loadData({
                projects: projectDataPage1.concat(projectDataPage2),
                models: modelsDataPage1.concat(modelsDataPage2),
                users: users,
                details: siteDetails
            });
        },

        routes() {
            const baseUrl = process.env.VITE_AWS_BASE_URL;

            // #region Project Routes

            this.get(baseUrl + "/projects", (schema: AppSchema, request) => {
                if (request?.queryParams?.["owner"]) {
                    const response: IPaginatedResponse<IProject> = {
                        ...paginatedProjectData1,
                        data: schema.db.projects.where({ ownerid: request.queryParams["owner"] })
                    };

                    return new Response(200, undefined, response);
                }

                switch (request?.queryParams?.["pageNumber"]) {
                    case "1": {
                        const page1: IPaginatedResponse<IProject> = {
                            ...paginatedProjectData1,
                            data: schema.db.projects
                        };

                        return new Response(200, undefined, page1);
                    }
                    case "2": {
                        const page2: IPaginatedResponse<IProject> = {
                            ...paginatedProjectData2,
                            data: schema.db.projects.filter(project =>
                                projectDataPage2.map(p => p.id).includes(project.id)
                            )
                        };

                        return new Response(200, undefined, page2);
                    }
                }

                return new Response(200, undefined, paginatedProjectData1);
            });

            this.post(baseUrl + "/projects", () => {
                return new Response(200, undefined, { id: v4() });
            });

            this.get(baseUrl + "/projects/:projectId", (schema, request) => {
                return schema.db.projects.findBy({ id: request.params.projectId });
            });

            this.put(baseUrl + "/projects/:projectId", (schema, request) => {
                const project = schema.db.projects.findBy({ id: request.params.projectId });

                const body = JSON.parse(request.requestBody);

                schema.db.projects.update(project.id, {
                    name: body.name,
                    description: body.description
                });

                return new Response(200);
            });

            this.delete(baseUrl + "/projects/:projectId", (schema, request) => {
                schema.db.projects.remove({ id: request.params.projectId });

                return new Response(204);
            });

            this.get(baseUrl + "/projects/:projectId/image/status", () => {
                return new Response(200, undefined, imagingProjectStatus);
            });

            // #endregion
            // #region Cohort Query Routes

            this.post(`${baseUrl}/step/cohort`, () => {
                const response: IGenericResponse = {
                    body: JSON.stringify({
                        queryId: v4(),
                        trust: [{
                            name: "KCH",
                            statusCode: 200
                        }, {
                            name: "UCLH",
                            statusCode: 200
                        }]
                    })
                };

                return new Response(200, undefined, response);
            });

            this.get(`${baseUrl}/cohort/:queryId`, () => {
                return new Response(200, undefined, OMOPResults);
            });
            // #endregion
            // #region Model Routes

            this.get(baseUrl + "/projects/:id/models", (_schema: AppSchema, request) => {
                if (request?.queryParams?.["pageSize"] === "5") {
                    const page1: IPaginatedResponse<IModel> = { ...latestModelData1 };

                    return new Response(200, undefined, page1);
                }

                switch (request?.queryParams?.["pageNumber"]) {
                    case "1": {
                        const page1: IPaginatedResponse<IModel> = { ...paginatedModelData1 };

                        return new Response(200, undefined, page1);
                    }
                    case "2": {
                        const page2: IPaginatedResponse<IModel> = { ...paginatedModelData2 };

                        return new Response(200, undefined, page2);
                    }
                }

                return new Response(200, undefined, paginatedModelData1);
            });

            this.post(baseUrl + "/step/model/:id", () => {
                return new Response(200, undefined, mockModelDashboard);
            });

            this.post(baseUrl + "/model/:id/initialise", () => {
                return new Response(200, undefined);
            });

            this.put(baseUrl + "/model/:id", () => {
                return new Response(200, undefined, mockModelDashboard);
            });

            this.delete(baseUrl + "/model/:id", (schema, request) => {
                schema.db.models.remove({ id: request.params.modelId });

                return new Response(204);
            });

            this.get(baseUrl + "/model/:modelId/logs", () => {
                return new Response(200, undefined, exampleLogs);
            });

            this.post(`${baseUrl}/model`, () => {
                return new Response(200, undefined, { id: v4() });
            });

            this.get(`${baseUrl}/model/:modelId/metrics`, () => {
                return new Response(200, undefined, ModelMetrics);
            });

            this.post(`${baseUrl}/model-files`, () => {
                return new Response(200, undefined, { id: v4() });
            });

            this.get(`${baseUrl}/files/:id`, (_schema, request) => {
                return new Response(200, undefined, [{
                    status: FileUploadStatus.COMPLETED,
                    id: request.params.id
                }]);
            });

            // #endregion
            // #region User Routes

            this.get(baseUrl + "/users/:userId/permissions", () => {
                return new Response(200, undefined, userPermissions);
            });

            this.get(baseUrl + "/users", () => {
                return new Response(200, undefined, paginatedUsers);
            });

            this.get(baseUrl + "/roles", () => {
                return new Response(200, undefined, allRoles);
            });

            this.get(baseUrl + "/users/:email", (schema: AppSchema, request) => {
                const user = schema.db.users.findBy({ email: request.params.email });

                if (!user) {
                    return new Response(404);
                }

                return new Response(200, undefined, user);
            });

            // #endregion

            this.get(baseUrl + "/trust/health", () => {
                return new Response(200, undefined, healthcheckResponse);
            });

            this.get(baseUrl + "/fl/status", () => {
                return new Response(200, undefined, connectedNets);
            });

            this.get(baseUrl + "/fl/:netName/status", () => {
                return new Response(200, undefined, detailedNetResponse);
            });

            this.get(baseUrl + "/site/details", (schema: AppSchema) => {
                return new Response(200, undefined, schema.db.details[0]);
            });

            this.put(baseUrl + "/site/details", (schema: AppSchema, request) => {
                const body = JSON.parse(request.requestBody);

                schema.db.details.update(1, { ...body });

                return new Response(200, undefined, schema.db.details.find(1));
            });

            this.get(baseUrl + "/trust", () => {
                return new Response(200, undefined, [{
                    name: "KCH",
                    id: "2001"
                }, {
                    name: "UCLH",
                    id: "2002"
                }]);
            });

            // Let cognito through
            this.passthrough("https://cognito-idp.eu-west-2.amazonaws.com/");

            // Let through everything else..
            this.passthrough(`${baseUrl}/**`);
        }
    });

    return server;
};
