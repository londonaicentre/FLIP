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

import { execFileSync } from "node:child_process";
import path from "node:path";

import { checkViteDemo, checkViteE2e, checkViteLocal, runCli } from "../check-build-flags.mjs";

// Resolved once because every spec runs the script as a child process,
// and resolving relative to __dirname keeps the test order-independent
// (vitest's cwd is not stable across runners).
const SCRIPT = path.resolve(__dirname, "../check-build-flags.mjs");

function makeIo(): { error: ReturnType<typeof vi.fn>; exit: ReturnType<typeof vi.fn> } {
    return {
        error: vi.fn(),
        exit: vi.fn()
    };
}

describe("checkViteLocal", () => {
    it("returns null when VITE_LOCAL is unset", () => {
        expect(checkViteLocal({})).toBeNull();
    });

    it("returns null when VITE_LOCAL is the string 'false'", () => {
        expect(checkViteLocal({ VITE_LOCAL: "false" })).toBeNull();
    });

    it("returns null for any value that is not exactly 'true'", () => {
        // Vite's build-time replacement only matches the exact string
        // "true" — anything else is dead-code-eliminated. Mirror that
        // here so the guard and the runtime stay in lockstep.
        expect(checkViteLocal({ VITE_LOCAL: "TRUE" })).toBeNull();
        expect(checkViteLocal({ VITE_LOCAL: "1" })).toBeNull();
        expect(checkViteLocal({ VITE_LOCAL: "yes" })).toBeNull();
        expect(checkViteLocal({ VITE_LOCAL: "" })).toBeNull();
    });

    it("returns a non-empty error message when VITE_LOCAL='true'", () => {
        const reason = checkViteLocal({ VITE_LOCAL: "true" });
        expect(reason).not.toBeNull();
        expect(reason).toContain("Refusing to build");
        expect(reason).toContain("VITE_LOCAL");
    });

    it("mentions Cognito so the operator sees the security stake", () => {
        // The whole point of failing the build is to flag the auth
        // bypass — a generic "config error" message would let an
        // operator paper over it by tweaking env vars without
        // understanding what they were turning off.
        const reason = checkViteLocal({ VITE_LOCAL: "true" });
        expect(reason).toMatch(/Cognito/i);
    });
});

describe("checkViteE2e", () => {
    it("names the Cypress auth seam so the stake is visible", () => {
        // A generic "bad config" message would invite an operator to paper
        // over it; the refusal has to say what the flag actually does.
        const reason = checkViteE2e();
        expect(reason).toContain("VITE_E2E");
        expect(reason).toContain("cypress.auth.user");
        expect(reason).toContain("/auth/login");
    });

    it("names .env.e2e as the one legitimate home", () => {
        expect(checkViteE2e()).toContain(".env.e2e");
    });

    it("reports mode and command when the caller knows them", () => {
        const reason = checkViteE2e("development", "serve");
        expect(reason).toContain("development");
        expect(reason).toContain("serve");
    });

    it("falls back to build phrasing when mode is unknown", () => {
        expect(checkViteE2e()).toContain("Refusing to build");
    });
});

describe("checkViteDemo", () => {
    // VITE_DEMO carries the same inlined auth-bypass semantics as
    // VITE_LOCAL (src/utils/auth.ts + the demo Mirage server in
    // src/main.ts), so it gets the same guard. The legitimate demo
    // build never sets the env var — `npm run build:demo` gets the
    // flag from vite.config's `define` for `--mode demo`.

    it("returns null when VITE_DEMO is unset", () => {
        expect(checkViteDemo({})).toBeNull();
    });

    it("returns null for any value that is not exactly 'true'", () => {
        expect(checkViteDemo({ VITE_DEMO: "false" })).toBeNull();
        expect(checkViteDemo({ VITE_DEMO: "TRUE" })).toBeNull();
        expect(checkViteDemo({ VITE_DEMO: "1" })).toBeNull();
        expect(checkViteDemo({ VITE_DEMO: "" })).toBeNull();
    });

    it("returns a non-empty error message when VITE_DEMO='true'", () => {
        const reason = checkViteDemo({ VITE_DEMO: "true" });
        expect(reason).not.toBeNull();
        expect(reason).toContain("Refusing to build");
        expect(reason).toContain("VITE_DEMO");
        expect(reason).toMatch(/Cognito/i);
    });

    it("points the operator at the supported demo-build path", () => {
        // The likely way this fires is someone trying to build the
        // demo by exporting the flag; the message must redirect them
        // to `npm run build:demo` rather than reading as a dead end.
        const reason = checkViteDemo({ VITE_DEMO: "true" });
        expect(reason).toContain("build:demo");
    });
});

describe("runCli", () => {
    // runCli is the exported CLI entry. We drive it directly (rather
    // than spawning the script as a child process) so vitest's
    // coverage instrumentation actually sees the lines execute —
    // child-process runs don't propagate to the v8 coverage report.

    it("returns 0 and does not call exit/error when VITE_LOCAL is unset", () => {
        const io = makeIo();

        const code = runCli({}, io);

        expect(code).toBe(0);
        expect(io.exit).not.toHaveBeenCalled();
        expect(io.error).not.toHaveBeenCalled();
    });

    it("returns 0 and does not call exit/error when VITE_LOCAL='false'", () => {
        const io = makeIo();

        const code = runCli({ VITE_LOCAL: "false" }, io);

        expect(code).toBe(0);
        expect(io.exit).not.toHaveBeenCalled();
        expect(io.error).not.toHaveBeenCalled();
    });

    it("returns 1 when VITE_E2E='true' — the prebuild hooks have no mode, so it can only be a shipping build", () => {
        const io = makeIo();

        const code = runCli({ VITE_E2E: "true" }, io);

        expect(code).toBe(1);
        expect(io.exit).toHaveBeenCalledWith(1);
        expect(io.error.mock.calls[0][0]).toContain("VITE_E2E");
    });

    it("returns 0 when VITE_E2E is not exactly 'true'", () => {
        const io = makeIo();

        expect(runCli({ VITE_E2E: "false" }, io)).toBe(0);
        expect(runCli({ VITE_E2E: "1" }, io)).toBe(0);
        expect(io.exit).not.toHaveBeenCalled();
    });

    it("returns 1 and forwards the reason to error+exit when VITE_LOCAL='true'", () => {
        const io = makeIo();

        const code = runCli({ VITE_LOCAL: "true" }, io);

        expect(code).toBe(1);
        expect(io.exit).toHaveBeenCalledWith(1);
        expect(io.error).toHaveBeenCalledTimes(1);
        const errMsg = io.error.mock.calls[0][0];
        expect(errMsg).toContain("Refusing to build");
        expect(errMsg).toMatch(/Cognito/i);
    });

    it("returns 1 and forwards the reason to error+exit when VITE_DEMO='true'", () => {
        const io = makeIo();

        const code = runCli({ VITE_DEMO: "true" }, io);

        expect(code).toBe(1);
        expect(io.exit).toHaveBeenCalledWith(1);
        expect(io.error).toHaveBeenCalledTimes(1);
        const errMsg = io.error.mock.calls[0][0];
        expect(errMsg).toContain("VITE_DEMO");
    });

    it("returns 0 when neither flag is set", () => {
        const io = makeIo();

        const code = runCli({ VITE_LOCAL: "false", VITE_DEMO: "false" }, io);

        expect(code).toBe(0);
        expect(io.exit).not.toHaveBeenCalled();
        expect(io.error).not.toHaveBeenCalled();
    });
});

describe("check-build-flags.mjs CLI (smoke)", () => {
    // Belt-and-braces contract test on the actual script invocation:
    // confirms the npm prebuild hook (`node scripts/check-build-flags.mjs`)
    // actually exits non-zero when the flag is set. The runCli unit
    // tests above cover the logic; this proves the wiring.

    it("exits non-zero on stderr when VITE_LOCAL='true'", () => {
        const env = {
            ...process.env,
            VITE_LOCAL: "true"
        };

        let caught: { status: number | null; stderr: string } | null = null;
        try {
            execFileSync("node", [SCRIPT], {
                env,
                stdio: "pipe"
            });
        } catch (e) {
            const err = e as { status: number | null; stderr: Buffer };
            caught = {
                status: err.status,
                stderr: err.stderr.toString()
            };
        }

        expect(caught).not.toBeNull();
        expect(caught?.status).toBe(1);
        expect(caught?.stderr).toContain("Refusing to build");
    });

    it("exits non-zero on stderr when VITE_DEMO='true'", () => {
        const env = {
            ...process.env,
            VITE_DEMO: "true"
        };
        delete env.VITE_LOCAL;

        let caught: { status: number | null; stderr: string } | null = null;
        try {
            execFileSync("node", [SCRIPT], {
                env,
                stdio: "pipe"
            });
        } catch (e) {
            const err = e as { status: number | null; stderr: Buffer };
            caught = {
                status: err.status,
                stderr: err.stderr.toString()
            };
        }

        expect(caught).not.toBeNull();
        expect(caught?.status).toBe(1);
        expect(caught?.stderr).toContain("VITE_DEMO");
    });

    it("exits 0 when neither flag is set", () => {
        const env = { ...process.env };
        delete env.VITE_LOCAL;
        delete env.VITE_DEMO;

        expect(() => execFileSync("node", [SCRIPT], { env })).not.toThrow();
    });
});
