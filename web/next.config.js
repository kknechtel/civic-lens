/** @type {import('next').NextConfig} */
const nextConfig = {
  // standalone is only needed for the production bundle (Docker/Amplify
  // deploy). Setting it in dev mode breaks chunk resolution on Windows
  // ("Cannot find module './611.js'"), so we gate it behind NODE_ENV.
  ...(process.env.NODE_ENV === "production" ? { output: "standalone" } : {}),
  // API proxy handled by app/api/[...path]/route.ts
  // Note: `experimental.missingSuspenseWithCSRBailout` was removed in Next 15.
};

module.exports = nextConfig;
