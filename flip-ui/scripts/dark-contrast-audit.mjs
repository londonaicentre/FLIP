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

/*
 * Static dark-mode contrast audit for the FLIP UI.
 *
 * Because styling is class-based Tailwind (`darkMode: "class"`) with a closed
 * palette, every `dark:text-*` / `dark:bg-*` (and unconditional `text-*`/`bg-*`)
 * utility resolves to a known hex. This script walks the Vue templates, works out
 * each element's *effective dark-mode* text and background colour, and computes the
 * WCAG 2.1 contrast ratio. No browser or running stack required.
 *
 * What it reports:
 *   1. Surface matrix  — every effective dark-mode text colour vs the standard dark
 *      surfaces (gray-900/800/700), with usage counts. The "gray-500 fails on
 *      everything" view.
 *   2. Local failures  — elements that set BOTH a dark text and a dark background
 *      and fall below WCAG AA. Highest confidence, directly actionable (file:line).
 *   3. Base-only text  — elements with a `text-*` colour but NO `dark:text-*`
 *      override (the "forgot the dark variant" bug class), shown against surfaces.
 *
 * Thresholds: WCAG AA normal text 4.5:1, AA large/UI 3.0:1, AAA normal 7.0:1.
 *
 * Limitations (honest blind spots):
 *   - Only static `class="..."` attributes are parsed. Dynamic `:class` bindings
 *     (object/array syntax used by status badges etc.) are NOT attributed.
 *   - When an element sets no background of its own, its text is scored against the
 *     three standard surfaces rather than its true (inherited) ancestor background.
 *   - Opacity modifiers, gradients and image backgrounds are out of scope.
 *
 * Usage:  node scripts/dark-contrast-audit.mjs [--all] [--surface=gray-800]
 *   --all       also list local pairs that PASS (default: failures only)
 *   --surface   primary surface used for the headline verdict (default gray-800)
 * Exit code is non-zero when any AA failure is found (suitable as a CI gate).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");

const AA_NORMAL = 4.5;
const AA_LARGE = 3.0;
const AAA_NORMAL = 7.0;

const args = process.argv.slice(2);
const SHOW_ALL = args.includes("--all");
const SHOW_LOCATIONS = args.includes("--locations");
const surfaceArg = (args.find((a) => a.startsWith("--surface=")) || "").split("=")[1];
const PRIMARY_SURFACE = surfaceArg || "gray-800";

// ---------------------------------------------------------------------------
// Palette — Tailwind v3 defaults for the families actually used, plus the
// project customs from tailwind.config.js. `green` is aliased to `emerald`
// there (`green: colors.emerald`), so we mirror that.
// ---------------------------------------------------------------------------
const TW = {
    gray: {
        50: "#f9fafb", 100: "#f3f4f6", 200: "#e5e7eb", 300: "#d1d5db", 400: "#9ca3af",
        500: "#6b7280", 600: "#4b5563", 700: "#374151", 800: "#1f2937", 900: "#111827", 950: "#030712",
    },
    red: {
        50: "#fef2f2", 100: "#fee2e2", 200: "#fecaca", 300: "#fca5a5", 400: "#f87171",
        500: "#ef4444", 600: "#dc2626", 700: "#b91c1c", 800: "#991b1b", 900: "#7f1d1d", 950: "#450a0a",
    },
    emerald: {
        50: "#ecfdf5", 100: "#d1fae5", 200: "#a7f3d0", 300: "#6ee7b7", 400: "#34d399",
        500: "#10b981", 600: "#059669", 700: "#047857", 800: "#065f46", 900: "#064e3b", 950: "#022c22",
    },
    blue: {
        50: "#eff6ff", 100: "#dbeafe", 200: "#bfdbfe", 300: "#93c5fd", 400: "#60a5fa",
        500: "#3b82f6", 600: "#2563eb", 700: "#1d4ed8", 800: "#1e40af", 900: "#1e3a8a", 950: "#172554",
    },
    yellow: {
        50: "#fefce8", 100: "#fef9c3", 200: "#fef08a", 300: "#fde047", 400: "#facc15",
        500: "#eab308", 600: "#ca8a04", 700: "#a16207", 800: "#854d0e", 900: "#713f12", 950: "#422006",
    },
    amber: {
        50: "#fffbeb", 100: "#fef3c7", 200: "#fde68a", 300: "#fcd34d", 400: "#fbbf24",
        500: "#f59e0b", 600: "#d97706", 700: "#b45309", 800: "#92400e", 900: "#78350f", 950: "#451a03",
    },
};
TW.green = TW.emerald; // tailwind.config.js: `green: colors.emerald`

const CUSTOM = {
    primary: {
        100: "#F7F3F9", 200: "#DBC4E2", 300: "#B88AC6", 400: "#9452A8",
        500: "#61366e", 600: "#482852", 700: "#301A37", 800: "#180D1B", 900: "#040205",
    },
    lightgreen: { 100: "#DCEDC8", 900: "#33691E" },
    deeporange: { 900: "#BF360C" },
};
const SINGLE = { body: "#FAF7F5", white: "#ffffff", black: "#000000", transparent: null, current: null, inherit: null };

// Standard dark-mode surfaces in use (page bg / content bg / lighter panels).
const SURFACES = ["gray-900", "gray-800", "gray-700"];

function resolveColor(name) {
    if (name in SINGLE) return SINGLE[name];
    const arb = name.match(/^\[#([0-9a-fA-F]{3,8})\]$/);
    if (arb) {
        let h = arb[1];
        if (h.length === 3) h = h.split("").map((c) => c + c).join("");
        return "#" + h.slice(0, 6).toLowerCase();
    }
    const m = name.match(/^([a-z]+)-(\d+)$/);
    if (m) {
        const [, fam, shade] = m;
        if (CUSTOM[fam]?.[shade]) return CUSTOM[fam][shade];
        if (TW[fam]?.[shade]) return TW[fam][shade];
    }
    return null;
}

// ---------------------------------------------------------------------------
// WCAG 2.1 relative luminance + contrast ratio
// ---------------------------------------------------------------------------
function luminance(hex) {
    const c = hex.replace("#", "");
    const ch = [0, 2, 4].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
    const lin = ch.map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
function contrast(a, b) {
    const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (l1 + 0.05) / (l2 + 0.05);
}
function verdict(ratio) {
    if (ratio >= AAA_NORMAL) return "AAA";
    if (ratio >= AA_NORMAL) return "AA";
    if (ratio >= AA_LARGE) return "AA-large";
    return "FAIL";
}

// ---------------------------------------------------------------------------
// Parse static class="..." blocks and derive effective dark-mode colours
// ---------------------------------------------------------------------------
function pick(classes, kind) {
    // returns { dark, base } colour names for `text` or `bg`
    let dark = null;
    let base = null;
    for (const cls of classes) {
        let m;
        if ((m = cls.match(new RegExp(`^dark:${kind}-(.+)$`)))) {
            if (resolveColor(m[1]) !== null) dark = m[1];
        } else if (!cls.includes(":") && (m = cls.match(new RegExp(`^${kind}-(.+)$`)))) {
            if (resolveColor(m[1]) !== null) base = m[1];
        }
    }
    return { dark, base };
}

function* walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) yield* walk(full);
        else if (entry.name.endsWith(".vue")) yield full;
    }
}

const textUsage = new Map(); // effText colourName -> { explicit, baseOnly, examples:[] }
const localPairs = []; // { file, line, text, bg, ratio }
const baseOnly = new Map(); // base text colourName -> { count, examples:[] }

const reClass = /class="([^"]*)"/g;
for (const file of walk(SRC)) {
    const content = fs.readFileSync(file, "utf8");
    const rel = path.relative(path.resolve(__dirname, ".."), file);
    let m;
    while ((m = reClass.exec(content)) !== null) {
        const line = content.slice(0, m.index).split("\n").length;
        const classes = m[1].split(/\s+/).filter(Boolean);
        const t = pick(classes, "text");
        const b = pick(classes, "bg");
        const effText = t.dark ?? t.base;
        const effBg = b.dark ?? b.base;
        if (!effText) continue;

        // Track usage of every effective dark-mode text colour.
        const u = textUsage.get(effText) || { explicit: 0, baseOnly: 0, examples: [], locs: [] };
        if (t.dark) u.explicit++;
        else u.baseOnly++;
        if (u.examples.length < 3) u.examples.push(`${rel}:${line}`);
        u.locs.push({ loc: `${rel}:${line}`, explicit: !!t.dark, bg: effBg });
        textUsage.set(effText, u);

        // Base-only text (no dark: override) — the "forgot the dark variant" class.
        if (!t.dark && t.base) {
            const bo = baseOnly.get(t.base) || { count: 0, examples: [] };
            bo.count++;
            if (bo.examples.length < 4) bo.examples.push(`${rel}:${line}`);
            baseOnly.set(t.base, bo);
        }

        // Local pair: element sets both an effective text AND background colour.
        if (effBg) {
            const fg = resolveColor(effText);
            const bgHex = resolveColor(effBg);
            if (fg && bgHex) {
                localPairs.push({ file: rel, line, text: effText, bg: effBg, ratio: contrast(fg, bgHex) });
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
const pad = (s, n) => String(s).padEnd(n);
const padR = (s, n) => String(s).padStart(n);
const fmt = (r) => r.toFixed(2);
const hr = "-".repeat(76);

console.log(`\nFLIP UI — dark-mode contrast audit (WCAG 2.1)`);
console.log(`Scanned: ${path.relative(process.cwd(), SRC)}  |  primary surface: ${PRIMARY_SURFACE}`);
console.log(`Thresholds: AA normal ${AA_NORMAL}:1, AA large/UI ${AA_LARGE}:1, AAA ${AAA_NORMAL}:1\n`);

// 1. Surface matrix
console.log(hr);
console.log("1. EFFECTIVE DARK-MODE TEXT COLOURS vs STANDARD SURFACES");
console.log(hr);
console.log(`${pad("text colour", 18)}${pad("hex", 10)}${padR("uses", 6)}${padR("expl", 6)}${padR("base", 6)}  ` +
    SURFACES.map((s) => padR(s.replace("gray-", "g"), 7)).join("") + "   worst");
const sortedText = [...textUsage.entries()].sort((a, b) => {
    const ra = Math.min(...SURFACES.map((s) => contrast(resolveColor(a[0]), resolveColor(s))));
    const rb = Math.min(...SURFACES.map((s) => contrast(resolveColor(b[0]), resolveColor(s))));
    return ra - rb;
});
let anyFail = false;
for (const [name, u] of sortedText) {
    const fg = resolveColor(name);
    const ratios = SURFACES.map((s) => contrast(fg, resolveColor(s)));
    const worst = Math.min(...ratios);
    if (worst < AA_NORMAL) anyFail = true;
    const cells = ratios.map((r) => padR(fmt(r), 7)).join("");
    console.log(`${pad(name, 18)}${pad(fg, 10)}${padR(u.explicit + u.baseOnly, 6)}${padR(u.explicit, 6)}` +
        `${padR(u.baseOnly, 6)}  ${cells}   ${verdict(worst)}`);
}

// 2. Local failures (both fg+bg set on the same element)
const failingPairs = localPairs.filter((p) => p.ratio < AA_NORMAL && luminance(resolveColor(p.bg)) < 0.18);
// Dedupe identical colour pairs, keep all locations.
const grouped = new Map();
for (const p of failingPairs) {
    const key = `${p.text} on ${p.bg}`;
    const g = grouped.get(key) || { ratio: p.ratio, locs: [] };
    g.locs.push(`${p.file}:${p.line}`);
    grouped.set(key, g);
}
console.log("\n" + hr);
console.log("2. LOCAL PAIRS FAILING AA  (element sets both dark text AND dark bg)");
console.log(hr);
if (grouped.size === 0) {
    console.log("None — no element co-locates a failing dark text+bg pair.");
} else {
    for (const [key, g] of [...grouped.entries()].sort((a, b) => a[1].ratio - b[1].ratio)) {
        anyFail = true;
        console.log(`\n  ${key}  —  ${fmt(g.ratio)}:1  [${verdict(g.ratio)}]  (${g.locs.length} site${g.locs.length > 1 ? "s" : ""})`);
        for (const loc of g.locs.slice(0, 8)) console.log(`      ${loc}`);
        if (g.locs.length > 8) console.log(`      … +${g.locs.length - 8} more`);
    }
}

// 3. Base-only text colours (no dark: override)
console.log("\n" + hr);
console.log("3. TEXT WITH NO dark: OVERRIDE  (light-mode colour persists into dark mode)");
console.log(hr);
const boSorted = [...baseOnly.entries()]
    .map(([name, v]) => ({ name, ...v, worst: Math.min(...SURFACES.map((s) => contrast(resolveColor(name), resolveColor(s)))) }))
    .filter((x) => x.worst < AA_NORMAL)
    .sort((a, b) => a.worst - b.worst);
if (boSorted.length === 0) {
    console.log("None below AA against the standard surfaces.");
} else {
    console.log(`${pad("text colour", 18)}${pad("hex", 10)}${padR("uses", 6)}${padR("worst", 8)}  examples`);
    for (const x of boSorted) {
        anyFail = true;
        console.log(`${pad(x.name, 18)}${pad(resolveColor(x.name), 10)}${padR(x.count, 6)}${padR(fmt(x.worst), 8)}  ${x.examples.slice(0, 2).join("  ")}`);
    }
}

// 4. Full locations for every colour failing AA on the primary surface
if (SHOW_LOCATIONS) {
    const surfHex = resolveColor(PRIMARY_SURFACE);
    console.log("\n" + hr);
    console.log(`4. LOCATIONS — colours below AA on ${PRIMARY_SURFACE}  ([dark]=explicit dark:text-*, [base]=no dark override)`);
    console.log(hr);
    const failing = [...textUsage.entries()]
        .map(([name, u]) => ({ name, u, ratio: contrast(resolveColor(name), surfHex) }))
        .filter((x) => x.ratio < AA_NORMAL)
        .sort((a, b) => a.ratio - b.ratio);
    for (const { name, u, ratio } of failing) {
        console.log(`\n  ${name}  (${resolveColor(name)})  —  ${fmt(ratio)}:1 on ${PRIMARY_SURFACE}  —  ${u.locs.length} site(s)`);
        for (const l of u.locs) {
            console.log(`      [${l.explicit ? "dark" : "base"}]${l.bg ? ` on ${l.bg}` : "       "}  ${l.loc}`);
        }
    }
}

// Optionally list passing local pairs
if (SHOW_ALL) {
    console.log("\n" + hr);
    console.log("LOCAL PAIRS PASSING AA");
    console.log(hr);
    for (const p of localPairs.filter((p) => p.ratio >= AA_NORMAL)) {
        console.log(`  ${pad(`${p.text} on ${p.bg}`, 36)} ${fmt(p.ratio)}  ${p.file}:${p.line}`);
    }
}

console.log("\n" + hr);
console.log(`SUMMARY: ${sortedText.length} distinct effective text colours, ` +
    `${grouped.size} failing local pair type(s), ${boSorted.length} no-override colour(s) below AA.`);
console.log(hr + "\n");

process.exit(anyFail ? 1 : 0);
