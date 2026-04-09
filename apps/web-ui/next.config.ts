import type { NextConfig } from "next";

const compilerApi =
  process.env.COMPILER_API_INTERNAL_URL || "http://localhost:8000";
const accessControlApi =
  process.env.ACCESS_CONTROL_INTERNAL_URL || "http://localhost:8001";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      // Access-control routes (must come before the catch-all)
      {
        source: "/api/v1/authn/:path*",
        destination: `${accessControlApi}/api/v1/authn/:path*`,
      },
      {
        source: "/api/v1/authz/:path*",
        destination: `${accessControlApi}/api/v1/authz/:path*`,
      },
      {
        source: "/api/v1/audit/:path*",
        destination: `${accessControlApi}/api/v1/audit/:path*`,
      },
      {
        source: "/api/v1/gateway-binding/:path*",
        destination: `${accessControlApi}/api/v1/gateway-binding/:path*`,
      },
      // Compiler-API catch-all for remaining /api/v1 paths
      {
        source: "/api/v1/:path*",
        destination: `${compilerApi}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
