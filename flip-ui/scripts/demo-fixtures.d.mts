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

/** Types for scripts/demo-fixtures.mjs (FLIP#1746), for the TypeScript specs that import it. */

export interface Roster {
    sites: { id: string; name: string; code: string; region: string }[];
    user: { id: string; email: string; name: string; organisation: string; isDisabled: boolean };
    net: string;
}

export declare const ROSTER: Roster;
export declare const DENIED_NAMES: readonly string[];
export declare const DENIED_ADDRESSES: readonly string[];
export declare const DENIED_STRINGS: readonly string[];
export declare function demoId(id: string): string;
export declare function assertClean(payload: unknown, where: string, extraDenied?: readonly string[]): void;
export declare function scrubFiles(
    files: Record<string, unknown>,
    trustMap: Record<string, string | null>
): Record<string, unknown>;
export declare function scrubProjects(slugs?: readonly string[], demoDir?: string): string[];
export declare function checkFixtures(demoDir?: string): { problems: string[]; checked: number };
export declare function checkDist(
    distDir: string,
    extraDenied?: readonly string[]
): { problems: { file: string; hit: string }[]; checked: number };
export declare function runCli(
    argv: readonly string[],
    io: { log: (message: string) => void; error: (message: string) => void },
    options?: { demoDir?: string }
): number;
