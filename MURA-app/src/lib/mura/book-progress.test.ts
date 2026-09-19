import { describe, expect, it } from "vitest";
import {
  formatBookProgress,
  getBookStatusInfo,
  isBookGenerating,
} from "./book-progress";
import type { TranslationKey } from "@/lib/i18n";
import type { BookProgress } from "./book-api";

describe("book-progress truthful reporting", () => {
  const fakeT = (key: TranslationKey, vars?: Record<string, string | number>): string => {
    if (vars) {
      return `${key}:${JSON.stringify(vars)}`;
    }
    return key;
  };

  it("identifies active generating statuses correctly", () => {
    expect(isBookGenerating("queued")).toBe(true);
    expect(isBookGenerating("planning")).toBe(true);
    expect(isBookGenerating("writing")).toBe(true);
    expect(isBookGenerating("reviewing")).toBe(true);
    expect(isBookGenerating("exporting")).toBe(true);

    expect(isBookGenerating("draft")).toBe(false);
    expect(isBookGenerating("completed")).toBe(false);
    expect(isBookGenerating("failed")).toBe(false);
    expect(isBookGenerating("cancelled")).toBe(false);
  });

  it("maps status badges with proper semantics", () => {
    expect(getBookStatusInfo("completed").variant).toBe("default");
    expect(getBookStatusInfo("writing").variant).toBe("amber");
    expect(getBookStatusInfo("failed").variant).toBe("destructive");
    expect(getBookStatusInfo("cancelled").variant).toBe("outline");
  });

  it("formats stage progress with truthful chapter counters and no percentages", () => {
    const p1: BookProgress = {
      stage: "writing_chapter",
      chapters_total: 8,
      chapters_approved: 2,
      current_chapter_number: 3,
      current_chapter_title: "Chapter Three",
    };
    const formatted = formatBookProgress(p1, fakeT);
    expect(formatted).toContain("bookStageWritingChapter");
    expect(formatted).toContain('"current":3');
    expect(formatted).toContain('"total":8');
    // Absolute rule: Never return % or percentage
    expect(formatted).not.toContain("%");

    const p2: BookProgress = {
      stage: "reviewing_chapter",
      chapters_total: 10,
      chapters_approved: 4,
      current_chapter_number: 5,
      current_chapter_title: null,
    };
    expect(formatBookProgress(p2, fakeT)).toContain("bookStageReviewingChapter");

    const p3: BookProgress = {
      stage: "exporting_pdf",
      chapters_total: 5,
      chapters_approved: 5,
      current_chapter_number: null,
      current_chapter_title: null,
    };
    expect(formatBookProgress(p3, fakeT)).toBe("bookStageExportingPdf");
  });
});

