type Env = Record<string, string | undefined>;

function origin(value: string | undefined): string | null {
  if (!value) return null;
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function splitSources(value: string | undefined): string[] {
  return (value ?? "")
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function buildContentSecurityPolicy(
  env: Env = process.env,
): string {
  const production = env.NODE_ENV === "production";
  const supabaseOrigin = origin(env.NEXT_PUBLIC_SUPABASE_URL);

  const scriptSrc = [
    "'self'",
    "'unsafe-inline'",
    ...(production ? [] : ["'unsafe-eval'"]),
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    ...splitSources(env.MURA_CSP_SCRIPT_SRC),
  ];

  const connectSrc = [
    "'self'",
    ...(supabaseOrigin ? [supabaseOrigin, supabaseOrigin.replace(/^https:/, "wss:")] : []),
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    "wss://*.clerk.com",
    "https://*.ingest.sentry.io",
    "https://*.sentry.io",
    ...splitSources(env.MURA_CSP_CONNECT_SRC),
  ];

  return [
    "default-src 'self'",
    `script-src ${unique(scriptSrc).join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    `connect-src ${unique(connectSrc).join(" ")}`,
    "media-src 'self' blob: data:",
    "object-src 'none'",
    "frame-src 'self' https://*.clerk.accounts.dev https://*.clerk.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "upgrade-insecure-requests",
  ].join("; ");
}
