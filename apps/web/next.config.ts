import type { NextConfig } from "next";

// Dev proxy: /api/* -> FastAPI (prefix removed), so the browser only talks to the Next
// origin and cookies stay first-party. In production the reverse proxy does the same.
const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

// Cabeçalhos de segurança do M1. A CSP completa de scripts fica para o M8 (docs/STATUS.md).
const securityHeaders = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
];

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
