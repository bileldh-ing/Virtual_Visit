import type { NextConfig } from "next";

const api = process.env.ROOMSCAPE_API_INTERNAL_URL ?? "http://localhost:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // 'wasm-unsafe-eval': the Spark splat renderer (and its sort worker) compile WebAssembly.
      "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'" + (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""),
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "connect-src 'self' data: blob:",
      "worker-src 'self' blob:",
      "font-src 'self' data:",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
];

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: {
    // The /api/* rewrite goes through the Next proxy, which buffers request bodies
    // and truncates anything over 10 MB by default. Photo uploads are multipart
    // (up to 40 MB per file on the API side), so allow headroom; the client also
    // splits uploads into ~8 MB batches (lib/batches.ts).
    proxyClientMaxBodySize: "100mb",
  },
  async rewrites() {
    // The browser only ever talks to this origin; the API stays private behind it.
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/healthz-api", destination: `${api}/healthz` },
    ];
  },
  async headers() {
    return [
      { source: "/((?!embed).*)", headers: [...securityHeaders, { key: "X-Frame-Options", value: "DENY" }] },
      { source: "/embed/:path*", headers: securityHeaders },
    ];
  },
};

export default config;
