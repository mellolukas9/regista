import type { NextConfig } from "next";

// Dev proxy: /api/* -> FastAPI (prefix removed), so the browser only talks to the Next
// origin and cookies stay first-party. In production the reverse proxy does the same.
const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default nextConfig;
