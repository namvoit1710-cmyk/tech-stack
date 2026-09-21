import { defineConfig } from "vitest/config";
import { resolve } from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["tests/setupTests.ts"],
    globals: true,
    css: true, // lets you import CSS/modules without extra mocks
    testTimeout: 30000,
    // Console output from PASSING tests is suppressed; failing tests keep theirs.
    //
    // This is the fix for the recurring `[vitest-worker]: Timeout calling "onTaskUpdate"`
    // that killed exactly one random test file per CI run. Mechanism, from vitest 3.2.6's
    // own source rather than guesswork: the worker RPC lists eventNames
    // ["onUserConsoleLog", "onCollected", "onCancel"], and `onTaskUpdate` is not among
    // them - so it is a call that blocks awaiting the main process, with birpc's
    // DEFAULT_TIMEOUT of 6e4 (60s, never overridden). A worker only dies if the main
    // process fails to acknowledge a task update for a full minute.
    //
    // The main process spends that time writing. Run 31350609632 emitted 45,377 log lines
    // from this suite, 25,030 of them (55%) stack traces vitest attaches to console output
    // from tests that PASSED - antd `bordered` deprecation notices, act() warnings, and
    // fetch failures against 127.0.0.1:3000. Each is a synchronous write into a pipe that
    // GitHub is ingesting, competing with the RPC the workers are blocked on.
    //
    // "passed-only" rather than `true`: when a test fails its console output is exactly
    // what you need, so it is kept. Measured on a 20-file subset: 780 output lines -> 11.
    silent: "passed-only",
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "**/*.d.ts",
        "**/*.test.*",
        "**/*.spec.*",

        "tests/**",
        "e2e_tests/**",

        "node_modules/**",
        ".next/**",
        "out/**",

        "**/*.config.*",
        "postcss.config.*",
        "tailwind.config.*",
        "next.config.*",
      ],
    },
    exclude: ["e2e_tests/**", "node_modules/**"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx", "tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  define: {
    "import.meta.vitest": "undefined",
  },
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
});
