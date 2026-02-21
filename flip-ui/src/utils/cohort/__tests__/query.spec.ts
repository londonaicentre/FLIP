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




import { containsForbiddenCommands } from "@/utils/cohort/query";

describe("Query", () => {
    describe("containsForbiddenCommands", () => {
        it("returns true for each forbidden command", () => {
            const forbiddenCommands = [
                "alter user",
                "alter table",
                "alter database",
                "drop table",
                "drop user",
                "drop role",
                "drop database",
                "create table",
                "substring"
            ];

            forbiddenCommands.forEach((command) => {
                const result = containsForbiddenCommands(command);

                expect(result).toBeTruthy();
            });
        });

        it("returns true for each forbidden command ignoring case", () => {
            const forbiddenCommands = [
                "ALTER USER",
                "ALTER table",
                "alter DATABASE",
                "DrOp TaBlE",
                "DROP USER",
                "drop ROLE",
                "DROP database",
                "CREATE table",
                "SUBSTRING"
            ];

            forbiddenCommands.forEach((command) => {
                const result = containsForbiddenCommands(command);

                expect(result).toBeTruthy();
            });
        });

        it("returns false for valid command", () => {
            const command = "SELECT * FROM AValidTable;";

            const result = containsForbiddenCommands(command);

            expect(result).toBeFalsy();
        });

        it("returns the same response if the command is ran twice", () => {
            const command = "ALTER USER serviceaccount WITH PASSWORD 'new_password';";

            const result1 = containsForbiddenCommands(command);
            const result2 = containsForbiddenCommands(command);

            expect(result1).toBeTruthy();
            expect(result2).toBeTruthy();
        });
    });
});
