/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle so the Docker image stays small and
  // does not need the full node_modules tree at runtime.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  // Surface the API base URL at build time too (server components read it).
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  },
};

export default nextConfig;
