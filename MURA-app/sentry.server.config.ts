import * as Sentry from "@sentry/nextjs";

const SENSITIVE_KEYS = [
  "authorization",
  "cookie",
  "token",
  "secret",
  "password",
  "transcript",
  "prompt",
  "story",
  "quote",
  "email",
  "name",
];

function isSensitive(key: string): boolean {
  const lower = key.toLowerCase();
  return SENSITIVE_KEYS.some((sub) => lower.includes(sub));
}

function sanitizeObject(obj: Record<string, unknown>): Record<string, unknown> {
  const sanitized: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (isSensitive(k)) {
      sanitized[k] = "[REDACTED]";
    } else if (v && typeof v === "object" && !Array.isArray(v)) {
      sanitized[k] = sanitizeObject(v as Record<string, unknown>);
    } else {
      sanitized[k] = v;
    }
  }
  return sanitized;
}

Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NODE_ENV || "development",
  sendDefaultPii: false,
  tracesSampleRate: 0.05,
  beforeSend(event) {
    if (event.request) {
      // Never attach request bodies containing family stories or audio
      delete (event.request as { data?: unknown }).data;
      delete (event.request as { body?: unknown }).body;

      if (event.request.headers) {
        for (const key of Object.keys(event.request.headers)) {
          if (isSensitive(key)) {
            event.request.headers[key] = "[REDACTED]";
          }
        }
      }
    }
    if (event.extra) {
      event.extra = sanitizeObject(event.extra);
    }
    return event;
  },
});

