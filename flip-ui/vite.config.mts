

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

import iconStubPlugin from "./test/iconStubPlugin";

// https://vitejs.dev/config/
export default defineConfig(({ mode, command, isPreview }) => {

    const env = loadEnv(mode, process.cwd());

    // Public Ark+ demo build (`vite --mode demo` / `vite build --mode demo`).
    // The deployable build (and `vite preview`, which serves that build) lives
    // under /ark_demo/; the dev server stays at "/" so local eyeballing needs
    // no path juggling.
    const isDemo = mode === "demo";
    const demoBase = isDemo && (command === "build" || isPreview);

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

    // Same stakes for the public-demo flag: VITE_DEMO bypasses Cognito auth
    // and swaps the real API for the offline demo Mirage server
    // (mocks/demo-server.ts). The only supported way to set it is
    // `--mode demo`, where the `define` below inlines it deterministically —
    // so any build where it arrives through the environment instead is a
    // mis-configured production build, not a demo build.
    if (command === "build" && !isDemo && env.VITE_DEMO === "true") {
        throw new Error(
            "Refusing to build flip-ui: VITE_DEMO=true is set. " +
            "VITE_DEMO bypasses Cognito auth and boots the offline demo " +
            "MirageJS server; the only supported way to build the demo " +
            "bundle is `npm run build:demo` (vite build --mode demo). " +
            "Unset VITE_DEMO before running `vite build`."
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
        base: demoBase ? "/ark_demo/" : "/",
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
            // FLIP#748: under vitest, swap unplugin-icons for a synchronous stub of the
            // whole ~icons/* namespace — the real plugin's async icon modules race spec
            // teardown and fail all-green runs (see test/iconStubPlugin.ts).
            mode === "test"
                ? iconStubPlugin()
                : Icons({
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
        define: {
            ...envWithProcessPrefix,
            // Inline the demo flag deterministically for `--mode demo`, independent
            // of .env-file / process-env resolution quirks.
            ...(isDemo ? { "import.meta.env.VITE_DEMO": JSON.stringify("true") } : {})
        },
        build: {
            sourcemap: false,
            chunkSizeWarningLimit: 1024,
            // Demo build only: Vite's default assetsDir ("assets") would put the
            // SPA's own JS/CSS/font bundle at /ark_demo/assets/*, colliding with
            // the CloudFront behavior that routes /ark_demo/assets/* to the
            // separate demo-downloads S3 bucket (deploy/providers/AWS/cloudfront.tf,
            // "Public Ark+ demo download assets"). That behavior would intercept
            // the bundle's own JS/CSS requests and 403 them against the wrong
            // bucket, breaking the app before Vue ever mounts. Non-demo builds
            // keep Vite's default.
            assetsDir: isDemo ? "static" : "assets",
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
            server: {
                deps: {
                    // codemirror-editor-vue3 imports raw .css from node_modules;
                    // inline it so Vite transforms those instead of Node choking.
                    inline: ["codemirror-editor-vue3"]
                }
            },
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
