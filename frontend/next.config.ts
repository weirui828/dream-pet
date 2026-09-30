import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // minimal runtime image for Docker
};

export default nextConfig;
