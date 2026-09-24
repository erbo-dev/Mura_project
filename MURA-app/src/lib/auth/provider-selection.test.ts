import { describe, expect, it } from "vitest";
import { selectAuthProvider } from "@/lib/auth/provider-selection";

const SUPABASE = {
  NEXT_PUBLIC_SUPABASE_URL: "https://abcdef.supabase.co",
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key",
};

const CLERK = {
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_valid",
  CLERK_SECRET_KEY: "sk_test_valid",
};

const DEV = {
  MURA_DEV_AUTH: "true",
  DEV_AUTH_ISSUER: "http://127.0.0.1:3000/api/dev-auth",
  DEV_AUTH_AUDIENCE: "mura-core",
};

describe("production auth provider selection", () => {
  it("fails closed when production does not explicitly name a provider", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        ...SUPABASE,
        ...CLERK,
      }),
    ).toBe("none");
  });

  it("selects only the explicitly requested configured production provider", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        MURA_AUTH_PROVIDER: "supabase",
        ...SUPABASE,
        ...CLERK,
      }),
    ).toBe("supabase");

    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        MURA_AUTH_PROVIDER: "clerk",
        ...SUPABASE,
        ...CLERK,
      }),
    ).toBe("clerk");
  });

  it("fails closed when the selected provider is incomplete", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        MURA_AUTH_PROVIDER: "supabase",
        NEXT_PUBLIC_SUPABASE_URL: SUPABASE.NEXT_PUBLIC_SUPABASE_URL,
      }),
    ).toBe("none");
  });

  it("never permits dev auth in production or preview", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        MURA_AUTH_PROVIDER: "dev",
        ...DEV,
      }),
    ).toBe("none");
    expect(
      selectAuthProvider({
        NODE_ENV: "development",
        VERCEL_ENV: "preview",
        MURA_AUTH_PROVIDER: "dev",
        ...DEV,
      }),
    ).toBe("none");
  });

  it("retains explicit local dev auth for local development", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "development",
        MURA_AUTH_PROVIDER: "dev",
        ...DEV,
      }),
    ).toBe("dev");
  });

  it("keeps legacy inference only outside deployments", () => {
    expect(selectAuthProvider({ NODE_ENV: "development", ...SUPABASE })).toBe("supabase");
    expect(selectAuthProvider({ NODE_ENV: "development", ...CLERK })).toBe("clerk");
  });

  it("rejects unknown provider names", () => {
    expect(
      selectAuthProvider({
        NODE_ENV: "production",
        MURA_AUTH_PROVIDER: "whatever",
        ...SUPABASE,
      }),
    ).toBe("none");
  });
});
