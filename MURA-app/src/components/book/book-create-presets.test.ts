import { describe, expect, it } from "vitest";
import { PRESET_WORD_COUNTS } from "./BookCreateModal";

describe("book creation word count presets", () => {
  it("offers exactly the three canonical contract word counts", () => {
    const counts = PRESET_WORD_COUNTS.map((p) => p.words);
    expect(counts).toEqual([20000, 25000, 30000]);
  });

  it("includes the 25,000 standard default word count", () => {
    const hasDefault = PRESET_WORD_COUNTS.some((p) => p.words === 25000);
    expect(hasDefault).toBe(true);
  });

  it("has strictly ascending word counts", () => {
    for (let i = 0; i < PRESET_WORD_COUNTS.length - 1; i++) {
      expect(PRESET_WORD_COUNTS[i].words).toBeLessThan(PRESET_WORD_COUNTS[i + 1].words);
    }
  });

  it("binds each preset to valid translation keys", () => {
    expect(PRESET_WORD_COUNTS[0].labelKey).toBe("bookWordCountPresetShort");
    expect(PRESET_WORD_COUNTS[1].labelKey).toBe("bookWordCountPresetMedium");
    expect(PRESET_WORD_COUNTS[2].labelKey).toBe("bookWordCountPresetLong");
  });
});

