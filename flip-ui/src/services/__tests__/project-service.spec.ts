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

import { beforeEach, describe, expect, it, vi } from "vitest";

import { _http } from "@/services/api";
import { approveProject,
    createProject,
    deleteProject,
    editProject,
    getImagingProjectsStatus,
    getProject,
    getProjects,
    IProject,
    stageProject,
    unstageProject } from "@/services/project-service";

vi.mock("@/services/api", () => ({
    _http: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn()
    }
}));

describe("project-service", () => {
    beforeEach(() => {
        vi.mocked(_http.get).mockReset();
        vi.mocked(_http.post).mockReset();
        vi.mocked(_http.put).mockReset();
        vi.mocked(_http.delete).mockReset();
    });

    describe("getProject", () => {
        it("GETs the URL and returns the project body", async () => {
            const project: IProject = {
                id: "p-1",
                name: "Demo",
                description: "d",
                ownerId: "u-1",
                ownerEmail: "owner@example.com",
                creationtimestamp: "2025-01-01",
                users: [],
                status: "UNSTAGED"
            };
            vi.mocked(_http.get).mockResolvedValue({ data: project } as never);

            const result = await getProject("/project/p-1");

            expect(_http.get).toHaveBeenCalledWith("/project/p-1");
            expect(result).toEqual(project);
        });
    });

    describe("editProject", () => {
        it("PUTs name+description and returns the updated project", async () => {
            const updated = { id: "p-1" } as IProject;
            vi.mocked(_http.put).mockResolvedValue({ data: updated } as never);

            const result = await editProject("/project/p-1", {
                name: "New Name",
                description: "new desc"
            });

            expect(_http.put).toHaveBeenCalledWith("/project/p-1", {
                name: "New Name",
                description: "new desc"
            });
            expect(result).toBe(updated);
        });
    });

    describe("getProjects", () => {
        it("GETs the URL and returns the paginated body", async () => {
            const body = {
                page: 1,
                pageSize: 10,
                totalPages: 1,
                totalRecords: 0,
                data: []
            };
            vi.mocked(_http.get).mockResolvedValue({ data: body } as never);

            const result = await getProjects("/project?page=1");

            expect(_http.get).toHaveBeenCalledWith("/project?page=1");
            expect(result).toEqual(body);
        });
    });

    describe("createProject", () => {
        it("POSTs the payload and returns the new id", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: { id: "p-99" } } as never);

            const result = await createProject("/project", {
                name: "New",
                description: "d",
                users: ["u-1"],
                dicom_to_nifti: true
            });

            expect(_http.post).toHaveBeenCalledWith("/project", {
                name: "New",
                description: "d",
                users: ["u-1"],
                dicom_to_nifti: true
            });
            expect(result).toEqual({ id: "p-99" });
        });
    });

    describe("stage / approve / unstage", () => {
        it("stageProject POSTs the trusts list under the `trusts` key", async () => {
            // The backend expects { trusts: [...] }; tests pin the wire
            // shape because a regression here silently stages with zero
            // trusts approved (an empty cohort, not an obvious error).
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await stageProject("/project/p-1/stage", ["t-1", "t-2"]);

            expect(_http.post).toHaveBeenCalledWith(
                "/project/p-1/stage",
                { trusts: ["t-1", "t-2"] }
            );
        });

        it("unstageProject POSTs to the URL with no body", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await unstageProject("/project/p-1/unstage");

            expect(_http.post).toHaveBeenCalledWith("/project/p-1/unstage");
        });

        it("approveProject POSTs the trusts list", async () => {
            vi.mocked(_http.post).mockResolvedValue({ data: undefined } as never);

            await approveProject("/project/p-1/approve", ["t-1"]);

            expect(_http.post).toHaveBeenCalledWith(
                "/project/p-1/approve",
                { trusts: ["t-1"] }
            );
        });
    });

    describe("deleteProject", () => {
        it("DELETEs the URL", async () => {
            vi.mocked(_http.delete).mockResolvedValue({ data: undefined } as never);

            await deleteProject("/project/p-1");

            expect(_http.delete).toHaveBeenCalledWith("/project/p-1");
        });
    });

    describe("getImagingProjectsStatus", () => {
        it("returns the per-trust status list", async () => {
            const statuses = [
                {
                    trustId: "t-1",
                    trustName: "Trust_1",
                    projectCreationCompleted: true,
                    importStatus: {
                        successful: 1,
                        failed: 0,
                        processing: 0,
                        queued: 0,
                        queueFailed: 0
                    }
                }
            ];
            vi.mocked(_http.get).mockResolvedValue({ data: statuses } as never);

            const result = await getImagingProjectsStatus("/project/p-1/imaging-status");

            expect(_http.get).toHaveBeenCalledWith("/project/p-1/imaging-status");
            expect(result).toEqual(statuses);
        });
    });
});
