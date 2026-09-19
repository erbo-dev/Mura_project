import { describe, expect, it } from "vitest";
import { previewSentences } from "@/components/processing/transcript-preview";

describe("preview sentences", () => {
  it("keeps punctuation with its sentence", () => {
    expect(previewSentences("Бабушка жила в Алматы. Мы ездили летом! Помнишь?")).toEqual([
      "Бабушка жила в Алматы.",
      "Мы ездили летом!",
      "Помнишь?",
    ]);
  });

  it("does not drop an unterminated last sentence", () => {
    // Recognition often ends mid-thought; the tail is still the speaker's words.
    expect(previewSentences("Әжем Алматыда тұрды. потом мы поехали")).toEqual([
      "Әжем Алматыда тұрды.",
      "потом мы поехали",
    ]);
  });

  it("ignores stray whitespace", () => {
    expect(previewSentences("  \n ")).toEqual([]);
  });
});
