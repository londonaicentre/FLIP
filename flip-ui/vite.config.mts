

import vue from "@vitejs/plugin-vue";
import path from "path";
import IconsResolver from "unplugin-icons/resolver";
import Icons from "unplugin-icons/vite";
import Components from "unplugin-vue-components/vite";
import { defineConfig, loadEnv } from "vite";
import Pages from "vite-plugin-pages";
import progress from "vite-plugin-progress";
import Inspector from "vite-plugin-vue-inspector";
import Layouts from "vite-plugin-vue-layouts-next";
import svgLoader from "vite-svg-loader";

// https://vitejs.dev/config/
export default defineConfig(({ mode, command }) => {

    const env = loadEnv(mode, process.cwd());

    // Belt-and-braces guard for `vite build` invoked directly (bypassing
    // the npm prebuild hook in package.json). Vite inlines VITE_LOCAL at
    // build time and the auth-bypass branch in src/utils/auth.ts is then
    // dead-code-eliminated only when the flag is anything but "true" —
    // shipping a build with VITE_LOCAL=true would yield an unauthenticated
    // bundle. The dev server (`vite` / `command === "serve"`) is
    // unaffected, since that's the legitimate use of the flag.
    if (command === "build" && env.VITE_LOCAL === "true") {
        throw new Error(
            "Refusing to build flip-ui: VITE_LOCAL=true is set. " +
            "VITE_LOCAL bypasses Cognito auth and enables the MirageJS mock; " +
            "it must never be inlined into a production build. Unset it or " +
            "set it to 'false' before running `vite build`."
        );
    }

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
            // Lazy-load route components: each page becomes its own `() => import()`
            // chunk fetched on navigation, instead of being eagerly bundled into the
            // entry. This stops the public /auth/login route from shipping the entire
            // authenticated app. Pairs with the `charts` manualChunk split below — see
            // the router's onError handler for stale-chunk recovery.
            Pages({ importMode: "async" }),
            Layouts({ defaultLayout: "MainLayout" })
        ],
        server: {
            open: true,
            host: true,
            allowedHosts: [
                "app.flip.aicentre.co.uk",
                "stag.flip.aicentre.co.uk"
            ]
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
            rolldownOptions: {
                output: {
                    manualChunks: (id) => {
                        if (id.includes("/node_modules/vue/") || id.includes("/node_modules/vue-router/")) return "app";
                        if (id.includes("/node_modules/aws-amplify/")) return "aws";
                        // echarts (+ its zrender renderer) is only used by the
                        // authenticated metrics/cohort charts. Keep it in its own
                        // chunk so the login route — which needs vee-validate, also
                        // in `misc` — doesn't drag the charting library along with it.
                        if (id.includes("/node_modules/echarts/") || id.includes("/node_modules/zrender/")) return "charts";
                        if (id.includes("/node_modules/axios/") || id.includes("/node_modules/vee-validate/")) return "misc";
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
            include: ["src/**/*.spec.ts", "scripts/**/*.spec.ts"],
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
