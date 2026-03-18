

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

    const envWithProcessPrefix = Object.entries(env).reduce(
        (prev, [key, val]) => {
            return {
                ...prev,
                ["process.env." + key]: `"${val}"`
            };
        },
        {}
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
