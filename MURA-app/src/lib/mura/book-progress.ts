/**
 * Truthful progress and status formatting for Grounded Family Books.
 *
 * MURA Principle: Truthful progress -- stages and counters only.
 * NEVER invent or display percentages like "25%" or calculate arithmetic progress bars.
 * A book generation process has distinct discrete phases and chapter counters:
 * ("Writing chapter 3 of 12", "Verifying chapter 3 of 12", etc.).
 */

import type { TranslationKey } from "@/lib/i18n";
import type { BookProgress, BookStatus } from "@/lib/mura/book-api";

export type StatusBadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "amber";

export interface BookStatusInfo {
  labelKey: TranslationKey;
  variant: StatusBadgeVariant;
}

const ACTIVE_STATUSES: ReadonlySet<BookStatus> = new Set([
  "queued",
  "planning",
  "writing",
  "reviewing",
  "exporting",
]);

/** True when the book is actively being planned, written, reviewed, or exported. */
export function isBookGenerating(status: BookStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

/** Status badge mapping with consistent tone and semantics. */
export function getBookStatusInfo(status: BookStatus): BookStatusInfo {
  switch (status) {
    case "draft":
      return { labelKey: "bookStatusDraft", variant: "secondary" };
    case "queued":
      return { labelKey: "bookStatusQueued", variant: "secondary" };
    case "planning":
      return { labelKey: "bookStatusPlanning", variant: "amber" };
    case "writing":
      return { labelKey: "bookStatusWriting", variant: "amber" };
    case "reviewing":
      return { labelKey: "bookStatusReviewing", variant: "amber" };
    case "exporting":
      return { labelKey: "bookStatusExporting", variant: "amber" };
    case "completed":
      return { labelKey: "bookStatusCompleted", variant: "default" };
    case "failed":
      return { labelKey: "bookStatusFailed", variant: "destructive" };
    case "cancelled":
      return { labelKey: "bookStatusCancelled", variant: "outline" };
    default:
      return { labelKey: "bookStatusDraft", variant: "secondary" };
  }
}

/**
 * Format the truthful stage description using the provided translation function.
 * Note: Never calculates or returns percentages.
 */
export function formatBookProgress(
  progress: BookProgress,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): string {
  const current = progress.current_chapter_number ?? 1;
  const total = progress.chapters_total > 0 ? progress.chapters_total : 1;

  switch (progress.stage) {
    case "preparing_sources":
      return t("bookStagePreparingSources");
    case "planning":
      return t("bookStagePlanning");
    case "writing_chapter":
      return t("bookStageWritingChapter", { current, total });
    case "reviewing_chapter":
      return t("bookStageReviewingChapter", { current, total });
    case "repairing_chapter":
      return t("bookStageRepairingChapter", { current, total });
    case "updating_continuity":
      return t("bookStageUpdatingContinuity");
    case "exporting_pdf":
      return t("bookStageExportingPdf");
    case "exporting_epub":
      return t("bookStageExportingEpub");
    case "completed":
      return t("bookStageCompleted");
    case "failed":
      return t("bookStageFailed");
    case "cancelled":
      return t("bookStageCancelled");
    default:
      return t("bookStagePreparingSources");
  }
}

