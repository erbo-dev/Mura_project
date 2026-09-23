import { describe, expect, it } from "vitest";
import { buildContentSecurityPolicy } from "@/lib/security/csp";

describe("Content Security Policy", () => {
  it("removes unsafe-eval and wildcard network schemes in production", () => {
    const policy = buildContentSecurityPolicy({
      NODE_ENV: "production",
      NEXT_PUBLIC_SUPABASE_URL: "https://project.supabase.co",
    });

    expect(policy).not.toContain("'unsafe-eval'");
    expect(policy).not.toContain("connect-src 'self' https: wss:");
    expect(policy).toContain("https://project.supabase.co");
    expect(policy).toContain("wss://project.supabase.co");
    expect(policy).toContain("object-src 'none'");
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("upgrade-insecure-requests");
  });

  it("keeps unsafe-eval only for local development tooling", () => {
    const policy = buildContentSecurityPolicy({ NODE_ENV: "development" });
    expect(policy).toContain("'unsafe-eval'");
  });

  it("accepts explicit deployment-specific additions without broad https wildcards", () => {
    const policy = buildContentSecurityPolicy({
      NODE_ENV: "production",
      MURA_CSP_CONNECT_SRC: "https://telemetry.example.test wss://socket.example.test",
      MURA_CSP_SCRIPT_SRC: "https://scripts.example.test",
    });

    expect(policy).toContain("https://telemetry.example.test");
    expect(policy).toContain("wss://socket.example.test");
    expect(policy).toContain("https://scripts.example.test");
  });
});
