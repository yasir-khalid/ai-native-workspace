import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  devIndicators: false,
  turbopack: {
    // react-lang auto-imports the vendor inspector in development. Keep the
    // customer-facing workspace free of its CDN widget and Autofix promotion.
    resolveAlias: {
      "@openuidev/devtools": "./lib/openui/devtools-disabled.ts",
    },
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  webpack(config) {
    config.resolve.alias["@openuidev/devtools"] =
      `${process.cwd()}/lib/openui/devtools-disabled.ts`;
    return config;
  },
};

export default nextConfig;
