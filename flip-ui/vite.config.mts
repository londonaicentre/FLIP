

import vue from "@vitejs/plugin-vue";
import path from "path";
import IconsResolver from "unplugin-icons/resolver";
import Icons from "unplugin-icons/vite";
import Components from "unplugin-vue-components/vite";
import { defineConfig, loadEnv } from "vite";
import Pages from "vite-plugin-pages";
import progress from "vite-plugin-progress";
import Inspector from "vite-plugin-vue-inspector";
import Layouts from "vite-plugin-vue-layouts";
import svgLoader from "vite-svg-loader";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {

    const env = loadEnv(mode, process.cwd());
    const nodeEnv = mode === "development" || mode === "test" ? mode : "production";

    const envWithProcessPrefix = Object.entries(env).reduce(
        (prev, [key, val]) => {
            return {
                ...prev,
                ["process.env." + key]: JSON.stringify(val)
            };
        },
        {
            "process.env": "{}",
            "process.env.NODE_ENV": JSON.stringify(nodeEnv),
            "process.env.VITE_LOCAL": JSON.stringify(env.VITE_LOCAL ?? "false"),
            "process.env.VITE_AWS_REGION": JSON.stringify(env.VITE_AWS_REGION ?? ""),
            "process.env.VITE_AWS_USER_POOL_ID": JSON.stringify(env.VITE_AWS_USER_POOL_ID ?? ""),
            "process.env.VITE_AWS_CLIENT_ID": JSON.stringify(env.VITE_AWS_CLIENT_ID ?? "")
        }
    );

    return {
        plugins: [
            progress(),
            vue(
                {
                    template:
                    {
                        compilerOptions:
                            { isCustomElement: (tag) => tag.startsWith("amplify-") }
                    }
                }),
            Inspector(),
            Icons({
                compiler: "vue3",
                autoInstall: true
            }),
            Components({
                dts: false,
                resolvers: IconsResolver({ prefix: "icon" })
            }),
            svgLoader(),
            Pages({ importMode: "sync" }),
            Layouts({ defaultLayout: "MainLayout" })
        ],
        server: {
            open: true,
            host: true,
            allowedHosts: [
                "app.flip.aicentre.co.uk",
                "stag.flip.aicentre.co.uk",
            ],
        },
        resolve: {
            alias: [
                {
                    find: "@",
                    replacement: path.resolve(__dirname, "./src")
                },
                {
                    find: "@test",
                    replacement: path.resolve(__dirname, "./test")
                },
                {
                    find: "./runtimeConfig",
                    replacement: "./runtimeConfig.browser"
                }
            ]
        },
        define: envWithProcessPrefix,
        build: {
            target: "esnext",
            sourcemap: false,
            chunkSizeWarningLimit: 1024,
            rollupOptions: {
                output: {
                    manualChunks: {
                        "app": ["vue-router", "vue"],
                        "aws": ["aws-amplify"],
                        "misc": ["axios", "echarts", "vee-validate"]
                    }
                }
            }
        },
        optimizeDeps: {
            esbuildOptions: {
                target: "esnext"
            },
            include: [
                "echarts/charts",
                "echarts/components",
                "echarts/core",
                "echarts/renderers",
                "pinia",
                "vue",
                "vue-tippy",
                "vue-router",
                "@headlessui/vue",
                "vee-validate",
                "yup",
                "uuid"
            ]
        },
        test: {
            globals: true,
            environment: "jsdom",
            setupFiles: ["./test/setup.ts"],
            include: ["src/**/*.spec.ts"],
            coverage: { reporter: ["text", "json", "cobertura"] },
            deps: {
                optimizer: {
                    web: {
                        include: [
                            "echarts"
                        ]
                    }
                }
            }
        }
    };
});
