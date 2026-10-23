import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@rstest/core";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    root,
    globals: true,
    testEnvironment: "jsdom",
    include: ["src/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
});
