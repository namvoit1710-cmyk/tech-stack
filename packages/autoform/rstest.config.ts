import { defineNodeConfig } from "@ldc/rstest-config/node";

export default defineNodeConfig({
    withJsx: true,
    testEnvironment: "happy-dom",
    setupFiles: ["@ldc/rstest-config/setup-react"]
});
