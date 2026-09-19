"use client";

import { AlertCircle, CheckCircle2, Loader2, StopCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import {
  cancelBook,
  fetchBookStatus,
  type BookProgress,
  type BookStage,
} from "@/lib/mura/book-api";
import { formatBookProgress } from "@/lib/mura/book-progress";

interface BookProgressTrackerProps {
  familyId: string;
  bookId: string;
  initialProgress?: BookProgress;
  onCompleted?: () => void;
  onCancelled?: () => void;
  onFailed?: () => void;
}

const DEFAULT_PROGRESS: BookProgress = {
  stage: "preparing_sources",
  chapters_total: 0,
  chapters_approved: 0,
  current_chapter_number: null,
  current_chapter_title: null,
};

export function BookProgressTracker({
  familyId,
  bookId,
  initialProgress,
  onCompleted,
  onCancelled,
  onFailed,
}: BookProgressTrackerProps) {
  const { t } = useMuraI18n();

  const [progress, setProgress] = useState<BookProgress>(
    initialProgress ?? DEFAULT_PROGRESS,
  );
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const completedRef = useRef(false);

  const isTerminal = (stage: BookStage): boolean => {
    return stage === "completed" || stage === "failed" || stage === "cancelled";
  };

  useEffect(() => {
    let timerId: NodeJS.Timeout | null = null;
    let active = true;

    const poll = async () => {
      try {
        const nextProgress = await fetchBookStatus(familyId, bookId);
        if (!active) return;

        setProgress(nextProgress);

        if (nextProgress.stage === "completed" && !completedRef.current) {
          completedRef.current = true;
          onCompleted?.();
          return;
        }
        if (nextProgress.stage === "failed") {
          onFailed?.();
          return;
        }
        if (nextProgress.stage === "cancelled") {
          onCancelled?.();
          return;
        }

        if (!isTerminal(nextProgress.stage)) {
          timerId = setTimeout(poll, 3000);
        }
      } catch {
        if (!active) return;
        // Network blip, retry polling
        timerId = setTimeout(poll, 4000);
      }
    };

    if (!isTerminal(progress.stage)) {
      timerId = setTimeout(poll, 2500);
    }

    return () => {
      active = false;
      if (timerId) clearTimeout(timerId);
    };
  }, [familyId, bookId, progress.stage, onCompleted, onCancelled, onFailed]);

  const handleCancel = useCallback(async () => {
    if (!window.confirm(t("bookCancelConfirm"))) return;

    setCancelling(true);
    setCancelError(null);
    try {
      await cancelBook(familyId, bookId);
      setProgress((prev) => ({ ...prev, stage: "cancelled" }));
      onCancelled?.();
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Failed to cancel book");
    } finally {
      setCancelling(false);
    }
  }, [familyId, bookId, t, onCancelled]);

  const stageText = formatBookProgress(progress, t);

  return (
    <div className="mx-auto max-w-xl rounded-3xl border border-ink/10 bg-raised p-6 shadow-soft sm:p-8">
      <div className="flex items-center justify-between gap-4">
        <h3 className="text-section font-bold tracking-tight text-ink">
          {t("bookProgressHeading")}
        </h3>

        {!isTerminal(progress.stage) && (
          <Button
            type="button"
            variant="ghost"
            size="md"
            onClick={handleCancel}
            disabled={cancelling}
            className="text-muted hover:text-destructive hover:bg-destructive/10"
          >
            <StopCircle className="size-4 mr-1.5" />
            <span>{t("bookCancelButton")}</span>
          </Button>
        )}
      </div>

      {cancelError && (
        <div className="mt-4 rounded-xl bg-destructive/10 p-3 text-caption text-destructive">
          {cancelError}
        </div>
      )}

      {/* Honest state indication */}
      <div className="mt-6 flex items-start gap-4">
        {progress.stage === "completed" ? (
          <CheckCircle2 className="size-6 text-ink shrink-0 mt-0.5" />
        ) : progress.stage === "failed" || progress.stage === "cancelled" ? (
          <AlertCircle className="size-6 text-muted shrink-0 mt-0.5" />
        ) : (
          <Loader2 className="size-6 text-ink/70 animate-spin shrink-0 mt-0.5" />
        )}

        <div className="min-w-0 flex-1">
          <p className="text-item font-semibold text-ink leading-snug">
            {stageText}
          </p>

          {progress.current_chapter_title && (
            <p className="mt-1 text-meta text-muted italic">
              «{progress.current_chapter_title}»
            </p>
          )}

          {progress.chapters_total > 0 && (
            <p className="mt-2 text-caption text-muted font-medium">
              {t("booksCardChapters", {
                approved: progress.chapters_approved,
                total: progress.chapters_total,
              })}
            </p>
          )}
        </div>
      </div>

      {/* Discrete chapter pills (truthful counter visualization, NO percentage math) */}
      {progress.chapters_total > 0 && (
        <div className="mt-6 pt-4 border-t border-ink/[0.08]">
          <div className="flex flex-wrap gap-1.5">
            {Array.from({ length: progress.chapters_total }).map((_, idx) => {
              const chapterNum = idx + 1;
              const isApproved = chapterNum <= progress.chapters_approved;
              const isCurrent = chapterNum === progress.current_chapter_number;

              let bgClass = "bg-ink/10 text-muted";
              if (isApproved) {
                bgClass = "bg-ink text-raised font-semibold";
              } else if (isCurrent) {
                bgClass = "bg-sand text-ink border border-ink/40 font-semibold animate-pulse";
              }

              return (
                <div
                  key={chapterNum}
                  title={`Глава ${chapterNum}`}
                  className={`flex size-8 items-center justify-center rounded-lg text-caption transition-colors ${bgClass}`}
                >
                  {chapterNum}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
