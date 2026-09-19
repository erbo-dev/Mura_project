import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { liveRecognitionLanguageOptions } from "@/hooks/use-live-transcript";

describe("liveRecognitionLanguageOptions", () => {
  it("previews auto and mixed speech in Russian rather than showing nothing", () => {
    // Web Speech takes one language and has no RU+KK mode. Previewing in
    // Russian is an approximation; showing nothing left the screen silent
    // while someone spoke into it, which reads as a broken microphone.
    expect(liveRecognitionLanguageOptions("auto")).toEqual(["ru-RU", "ru"]);
    expect(liveRecognitionLanguageOptions("mixed")).toEqual(["ru-RU", "ru"]);
  });

  it("honours an explicitly chosen language first", () => {
    expect(liveRecognitionLanguageOptions("ru")).toEqual(["ru-RU", "ru"]);
    expect(liveRecognitionLanguageOptions("kk")[0]).toBe("kk-KZ");
  });

  it("falls back to a language the browser will actually accept", () => {
    // Chrome does not ship Kazakh recognition everywhere. Without a fallback
    // the preview dies on `language-not-supported` and never restarts.
    expect(liveRecognitionLanguageOptions("kk")).toContain("ru-RU");
  });

  it("never asks the recogniser for more than one language at a time", () => {
    // Each entry is a fallback, not a claim of simultaneous support: the
    // recogniser is handed exactly one `lang` and moves down the list only
    // when the browser refuses the previous one.
    for (const setting of ["auto", "mixed", "ru", "kk"] as const) {
      for (const code of liveRecognitionLanguageOptions(setting)) {
        expect(code).not.toMatch(/[,+]/);
      }
    }
  });
});

describe("the preview never claims to be the archive transcript", () => {
  const SRC = join("src");

  it("says the exact transcript arrives after processing", () => {
    const view = readFileSync(join(SRC, "components", "record", "live-transcript.tsx"), "utf8");
    // The preview is a different recogniser from the one that writes the
    // archive. A speaker watching approximate Kazakh appear must not be left
    // to assume this is the final text.
    expect(view).toContain("livePreviewNote");
  });

  it("is translated in every locale the app ships", () => {
    const i18n = readFileSync(join(SRC, "lib", "i18n.tsx"), "utf8");
    expect(i18n.match(/livePreviewNote:/g)?.length).toBe(3);
  });

  it("is never sent to the server as a transcript", () => {
    // The recorder uploads audio and metadata. If the browser's guess ever
    // reached Core it would become archive content, and the archive would hold
    // words no multilingual recogniser ever produced.
    const api = readFileSync(join(SRC, "lib", "mura", "core-api.ts"), "utf8");
    const form = api.slice(api.indexOf("new FormData()"), api.indexOf("new FormData()") + 700);

    expect(form).toContain('append("file"');
    expect(form).not.toMatch(/append\(\s*["']transcript/);
    expect(form).not.toContain("sentences");
  });
});
