import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";
import { buildContentSecurityPolicy } from "./src/lib/security/csp";

const securityHeaders = [
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(self), geolocation=(), payment=(), usb=()",
  },
  {
    key: "Content-Security-Policy",
    value: buildContentSecurityPolicy(),
  },
];

const nextConfig: NextConfig = {
  // The dev indicator defaults to bottom-left, which is exactly where the
  // desktop rail puts the language switcher — in development it sits on top of
  // it and makes visual verification of the rail impossible. Production is
  // unaffected; this only moves the dev-only overlay out of the way.
  devIndicators: { position: "bottom-right" },
  output: "standalone",
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default withSentryConfig(nextConfig, {
  silent: !process.env.CI,
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
});
