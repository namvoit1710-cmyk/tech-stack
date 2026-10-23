import { pluginModuleFederation } from "@module-federation/rsbuild-plugin";
import { defineConfig, loadEnv } from "@rsbuild/core";
import { pluginReact } from "@rsbuild/plugin-react";
import path from "node:path";

const { publicVars } = loadEnv({ cwd: path.resolve(__dirname, "../..") });

export default defineConfig({
    plugins: [
        pluginReact(),
        pluginModuleFederation({
            name: "workflow_management",
            exposes: {
                "./workflow-management": "./src/app-with-provider.tsx",
            },
            remotes: {},
            shared: {
                "@ldc/mf-app": { singleton: true, eager: true, requiredVersion: false },
                "@ldc/mf-auth": { singleton: true, eager: true, requiredVersion: false },
                react: { singleton: true, eager: true, requiredVersion: "^19" },
                "react-dom": { singleton: true, eager: true, requiredVersion: "^19" },
                "react-router-dom": { singleton: true, eager: true, requiredVersion: false },
/*                 "react-i18next": {
                    singleton: true,
                    eager: true,
                    requiredVersion: false,
                },
                "i18next": {
                    singleton: true,
                    eager: true,
                    requiredVersion: false,
                }, */
            },
            // dts: {
            //   generateTypes: {
            //     abortOnError: false,
            //   },
            //   consumeTypes: {
            //     abortOnError: false,
            //   },
            // },
            dts: false,
            dev: {
                disableLiveReload: true,
            },
        }),
    ],
    tools: {
        postcss: {
            postcssOptions: {
                plugins: [
                    require("@tailwindcss/postcss")
                ],
            },
        },
        rspack: {
            experiments: {
                css: true
            },
        }
    },

    resolve: {
        alias: { "@": "./src" },
    },
    source: {
        define: publicVars,
        entry: { index: "./src/main.tsx" },
    },
    server: {
        port: 3004,
        host: '0.0.0.0',
        cors: {
            origin: '*',
            credentials: true,
        },
        historyApiFallback: true
    },

    html: {
        title: "Workflow Management",
        template: "./index.html"
    },

    dev: {
        client: {
            overlay: false,
        },
        lazyCompilation: false,
        hmr: true,
    },

    output: {
        injectStyles: false,
        assetPrefix: "auto",
    }
});
