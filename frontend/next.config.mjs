import process from "node:process";
import { URL } from "node:url";
const profile = process.env.NEXT_PUBLIC_SUPPORTLENS_PROFILE;
if (profile && profile !== "development" && profile !== "synthetic_demo") {
  throw new Error("Unsupported public SupportLens release profile.");
}
if (profile === "synthetic_demo") {
  const api = new URL(process.env.NEXT_PUBLIC_API_BASE_URL || "");
  const project = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  if (
    api.protocol !== "https:" ||
    api.origin !== api.href.replace(/\/$/, "") ||
    !project ||
    project === "supportlens-fc39b"
  ) {
    throw new Error(
      "The synthetic demo needs a dedicated Firebase project and exact HTTPS API origin.",
    );
  }
}
/** @type {import('next').NextConfig} */
const config = {
  distDir:
    process.env.SUPPORTLENS_BUILD_CONTEXT === "e2e" ? ".next-e2e" : ".next",
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          ...(profile === "synthetic_demo"
            ? [{ key: "Strict-Transport-Security", value: "max-age=31536000" }]
            : []),
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};
export default config;
