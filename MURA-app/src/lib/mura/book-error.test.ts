import { describe, expect, it } from "vitest";
import type { TranslationKey } from "@/lib/i18n";
import { getHumanBookError } from "./book-error";

describe("book error humanization", () => {
  const fakeT = (key: TranslationKey): string => `[translated:${key}]`;

  it("maps engine unavailable error to friendly notice", () => {
    const message = getHumanBookError("ENGINE_UNAVAILABLE", fakeT);
    expect(message).toBe("[translated:bookErrorEngineUnavailable]");
  });

  it("maps render and export failure errors to render error notice", () => {
    expect(getHumanBookError("RENDER_FAILED", fakeT)).toBe(
      "[translated:bookErrorRenderFailed]",
    );
    expect(getHumanBookError("EXPORT_FAILED", fakeT)).toBe(
      "[translated:bookErrorRenderFailed]",
    );
  });

  it("maps null, empty, or unknown errors to default book error message", () => {
    expect(getHumanBookError(null, fakeT)).toBe("[translated:bookErrorDefault]");
    expect(getHumanBookError("", fakeT)).toBe("[translated:bookErrorDefault]");
    expect(getHumanBookError("UNKNOWN_BACKEND_ERR", fakeT)).toBe(
      "[translated:bookErrorDefault]",
    );
  });

  it("never leaks raw error codes into user-facing output", () => {
    const rawCode = "CRITICAL_PLANNER_FAIL_CODE_123";
    const result = getHumanBookError(rawCode, fakeT);
    expect(result).not.toContain(rawCode);
    expect(result).not.toContain("Код ошибки");
  });
});

