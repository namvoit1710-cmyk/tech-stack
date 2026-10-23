// SA-2471: pure helpers for the section shared-schema editor (kept out of the
// component so they can be unit-tested without a DOM).
import type { IEnvironment, IEnvSectionKey } from "@/features/environments/types/environment";

// A credential value stored as the "no credential" sentinel counts as empty.
export const NONE_CRED = "__none__";

// How many of the section's shared keys this environment has a non-empty value for
// (drives the completeness badge, e.g. "3/5").
export const filledCount = (env: IEnvironment, keys: IEnvSectionKey[]): number =>
    keys.filter(k => {
        const v = env.variables.find(x => x.key === k.key)?.value ?? "";
        return v !== "" && v !== NONE_CRED;
    }).length;

// Optimistic client-side check → the ROW INDEXES whose key is empty or a
// case-insensitive duplicate. Instant feedback only; the backend Save (422
// SECTION_SCHEMA_INVALID_KEYS) remains authoritative.
export const optimisticBadKeyIndexes = (keys: { key: string }[]): Set<number> => {
    const bad = new Set<number>();
    const seen = new Map<string, number>();
    keys.forEach((r, i) => {
        const k = r.key.trim();
        if (!k) {
            bad.add(i);
            return;
        }
        const lower = k.toLowerCase();
        const prev = seen.get(lower);
        if (prev !== undefined) {
            bad.add(i);
            bad.add(prev);
        } else {
            seen.set(lower, i);
        }
    });
    return bad;
};
