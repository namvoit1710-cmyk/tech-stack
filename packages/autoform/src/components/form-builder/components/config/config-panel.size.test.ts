import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";

const CONFIG_DIR = join(__dirname);
const MAX_LINES = 300;

function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) return sourceFiles(full);
        if (!/\.tsx?$/.test(name)) return [];
        if (/\.test\.tsx?$/.test(name)) return [];
        return [full];
    });
}

describe("config folder file sizes (AC 8)", () => {
    it("keeps every source file under 300 lines", () => {
        const offenders = sourceFiles(CONFIG_DIR)
            .map((f) => ({
                file: f.replace(CONFIG_DIR, ""),
                lines: readFileSync(f, "utf8").split("\n").length,
            }))
            .filter((f) => f.lines > MAX_LINES);

        expect(offenders).toEqual([]);
    });

    it("reduces config-panel.tsx to a dispatcher", () => {
        const lines = readFileSync(join(CONFIG_DIR, "config-panel.tsx"), "utf8").split("\n").length;
        expect(lines).toBeLessThan(100);
    });
});
