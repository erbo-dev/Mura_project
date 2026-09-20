import type { useMuraI18n } from "@/lib/i18n";

/**
 * Humanizes book generation error codes into compassionate, helpful guidance.
 *
 * Never leaks raw technical error strings (such as ERR_500, ENGINE_UNAVAILABLE,
 * RENDER_FAILED, or database traces) into the user interface.
 */
export function getHumanBookError(
  errorCode: string | null | undefined,
  t: ReturnType<typeof useMuraI18n>["t"],
): string {
  if (!errorCode) return t("bookErrorDefault");
  const upper = errorCode.toUpperCase();
  if (upper.includes("ENGINE")) return t("bookErrorEngineUnavailable");
  if (upper.includes("RENDER") || upper.includes("EXPORT")) return t("bookErrorRenderFailed");
  return t("bookErrorDefault");
}

