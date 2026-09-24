import { isClerkConfigured } from "@/lib/auth/providers/clerk/config";
import { isDevAuthAllowed } from "@/lib/auth/providers/dev/config";
import { isSupabaseAuthConfigured } from "@/lib/auth/providers/supabase/config";

export type SelectedAuthProvider = "clerk" | "dev" | "supabase" | "none";
export type ConfiguredAuthProvider = Exclude<SelectedAuthProvider, "none">;

const VALID = new Set<ConfiguredAuthProvider>(["clerk", "dev", "supabase"]);

function isDeployment(env: Record<string, string | undefined>): boolean {
  return env.NODE_ENV === "production" || Boolean(env.VERCEL_ENV && env.VERCEL_ENV !== "development");
}

function providerReady(
  provider: ConfiguredAuthProvider,
  env: Record<string, string | undefined>,
): boolean {
  if (provider === "dev") return isDevAuthAllowed(env);
  if (provider === "supabase") return isSupabaseAuthConfigured(env);
  return isClerkConfigured(env);
}

/**
 * One deterministic provider decision shared by layout and server proxy.
 *
 * Production/preview must name MURA_AUTH_PROVIDER explicitly. Local development
 * keeps the historical inference order for convenience, but a missing
 * production setting fails closed instead of silently selecting whichever
 * credentials happen to exist.
 */
export function selectAuthProvider(
  env: Record<string, string | undefined> = process.env,
): SelectedAuthProvider {
  const requested = env.MURA_AUTH_PROVIDER?.trim().toLowerCase();
  if (requested) {
    if (!VALID.has(requested as ConfiguredAuthProvider)) return "none";
    const provider = requested as ConfiguredAuthProvider;
    return providerReady(provider, env) ? provider : "none";
  }

  if (isDeployment(env)) return "none";
  if (isDevAuthAllowed(env)) return "dev";
  if (isSupabaseAuthConfigured(env)) return "supabase";
  if (isClerkConfigured(env)) return "clerk";
  return "none";
}
