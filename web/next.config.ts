import type { NextConfig } from "next";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8080";

const nextConfig: NextConfig = {
  async rewrites() {
    // Proxy API + remaining Jinja app pages so the browser stays same-origin.
    return [
      {
        source: "/v1/:path*",
        destination: `${apiBase}/v1/:path*`,
      },
      {
        source: "/static/:path*",
        destination: `${apiBase}/static/:path*`,
      },
      {
        source: "/app/:path*",
        destination: `${apiBase}/app/:path*`,
      },
      {
        source: "/health",
        destination: `${apiBase}/health`,
      },
    ];
  },
};

export default nextConfig;
