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

import { getInitials } from "@/utils/helpers";

/**
 * Role colour-coding for the admin user-management screens.
 *
 * Roles arrive from the API as free-text names (`Admin`, `Researcher`, ...), so
 * tones are keyed by role name rather than by id. FLIP ships three roles today;
 * `purple` and `navy` are held in reserve for future roles and reached via a
 * deterministic fallback so any new role always renders a stable colour.
 */

/** The five role tones from the design handoff. */
export type RoleToneName = "magenta" | "purple" | "steel" | "navy" | "gray";

/** A single role tone: foreground (text + dot), background tint and avatar ring. */
export interface RoleTone {
    fg: string;
    bg: string;
    ring: string;
}

/** Hex ramps for every role tone. */
export const ROLE_TONES: Record<RoleToneName, RoleTone> = {
    magenta: {
        fg: "#9F1751",
        bg: "#FCE7F1",
        ring: "#F5C5DA"
    },
    purple: {
        fg: "#482852",
        bg: "#F2E7F5",
        ring: "#DBC4E2"
    },
    steel: {
        fg: "#34557A",
        bg: "#E7EEF6",
        ring: "#C5D6E8"
    },
    navy: {
        fg: "#1F2F55",
        bg: "#E5E9F3",
        ring: "#C2CAE0"
    },
    gray: {
        fg: "#4B5563",
        bg: "#F3F4F6",
        ring: "#E5E7EB"
    }
};

/** Explicit tone for each known role name (lower-cased keys). */
const NAMED_ROLE_TONES: Record<string, RoleToneName> = {
    "admin": "magenta",
    "administrator": "magenta",
    "researcher": "steel",
    "observer": "gray",
    "viewer": "gray",
    "manager": "purple",
    "project manager": "purple",
    "trust liaison": "navy"
};

/** Tones an unknown role may be assigned, picked deterministically by name hash. */
const FALLBACK_TONES: RoleToneName[] = ["purple", "navy", "steel", "magenta", "gray"];

/** Honorifics dropped before deriving a person's initials. */
const HONORIFICS = new Set([
    "dr", "prof", "professor", "mr", "mrs", "ms", "mx", "miss", "sir", "dame"
]);

/**
 * Resolve a role name to its tone name.
 *
 * Args:
 *     roleName (string | null | undefined): The role's display name.
 *
 * Returns:
 *     RoleToneName: The matched tone, or a deterministic fallback for unknown roles.
 */
export function roleToneName(roleName: string | null | undefined): RoleToneName {
    const key = (roleName ?? "").trim().toLowerCase();
    if (!key) {
        return "gray";
    }
    if (key in NAMED_ROLE_TONES) {
        return NAMED_ROLE_TONES[key];
    }
    // Hash the name so a given role always lands on the same fallback tone.
    let hash = 0;
    for (let i = 0; i < key.length; i++) {
        hash = (hash * 31 + key.charCodeAt(i)) | 0;
    }

    return FALLBACK_TONES[Math.abs(hash) % FALLBACK_TONES.length];
}

/**
 * Resolve a role name to its tone (fg/bg/ring hex values).
 *
 * Args:
 *     roleName (string | null | undefined): The role's display name.
 *
 * Returns:
 *     RoleTone: The tone object for the resolved tone name.
 */
export function roleTone(roleName: string | null | undefined): RoleTone {
    return ROLE_TONES[roleToneName(roleName)];
}

/**
 * Derive up-to-two-character initials for a user's avatar.
 *
 * Honorifics are stripped from the name first ("Dr Amara Okonkwo" -> "AO"). When
 * no usable name is present the email's local part is used instead, split on its
 * separators ("tomas.ribeiro@nhs.uk" -> "TR").
 *
 * Args:
 *     name (string | null | undefined): The user's full name.
 *     email (string | null | undefined): The user's email, used as a fallback.
 *
 * Returns:
 *     str: The initials, or "?" when neither input yields anything usable.
 */
export function userInitials(name: string | null | undefined, email: string | null | undefined): string {
    const meaningfulName = (name ?? "")
        .split(/\s+/)
        .filter((word) => word && !HONORIFICS.has(word.replace(/\./g, "").toLowerCase()))
        .join(" ");
    if (meaningfulName) {
        const initials = getInitials(meaningfulName);
        if (initials) {
            return initials;
        }
    }

    // Email local parts follow `first-initial.lastname` patterns where the first
    // segment is often a single letter, so derive initials per-segment rather
    // than via getInitials (which discards one-letter words).
    const emailSegments = (email ?? "").split("@")[0].split(/[._+-]+/).filter(Boolean);
    if (emailSegments.length >= 2) {
        return (emailSegments[0][0] + emailSegments[emailSegments.length - 1][0]).toUpperCase();
    }
    if (emailSegments.length === 1) {
        return emailSegments[0].slice(0, 2).toUpperCase();
    }

    return "?";
}
