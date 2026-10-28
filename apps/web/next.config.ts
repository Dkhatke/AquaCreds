import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  // The workspace package ships TypeScript sources.
  transpilePackages: ["@aquacreds/api-client"],
  typescript: {
    // `next build` cannot run TypeScript 7 (it has no compiler API). Type checking runs as its
    // own task (`pnpm typecheck` → tsc 7) in CI and turbo, so the build skips it.
    ignoreBuildErrors: true,
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
