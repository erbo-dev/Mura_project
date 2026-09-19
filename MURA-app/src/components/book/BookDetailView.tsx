"use client";

import { AlertCircle, ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { ArchiveState } from "@/components/archive/archive-state";
import { FamilyGate } from "@/components/family/family-gate";
import { AppHeader } from "@/components/layout/app-header";
import { PageContainer } from "@/components/shell/page-container";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import {
  fetchBook,
  fetchBookChapters,
  regenerateBook,
  type BookChapterPage,
  type BookChapterSummary,
  type BookDetail,
} from "@/lib/mura/book-api";
import { isBookGenerating } from "@/lib/mura/book-progress";
import { useArchiveResource } from "@/lib/mura/use-archive";
import { BookExportDownloads } from "./BookExportDownloads";
import { BookProgressTracker } from "./BookProgressTracker";
import { BookReader } from "./BookReader";

interface BookDetailContentProps {
  bookId: string;
}

function BookDetailContent({ bookId }: BookDetailContentProps) {
  const { t } = useMuraI18n();
  const router = useRouter();

  const [regenerating, setRegenerating] = useState(false);
  const [regenError, setRegenError] = useState<string | null>(null);

  const loadBook = useCallback(
    (familyId: string, signal: AbortSignal): Promise<BookDetail> => {
      return fetchBook(familyId, bookId, signal);
    },
    [bookId],
  );

  const bookResource = useArchiveResource<BookDetail>(loadBook);
  const book = bookResource.data;

  // Load chapters when book is completed
  const loadChapters = useCallback(
    async (familyId: string, signal: AbortSignal): Promise<BookChapterSummary[]> => {
      const resp: BookChapterPage = await fetchBookChapters(familyId, bookId, signal);
      return resp.items;
    },
    [bookId],
  );

  const chaptersResource = useArchiveResource<BookChapterSummary[]>(loadChapters, {
    enabled: !!book && book.status === "completed",
  });

  const handleRegenerate = useCallback(async () => {
    if (!bookResource.familyId) return;

    setRegenerating(true);
    setRegenError(null);
    try {
      const accepted = await regenerateBook(bookResource.familyId, bookId);
      router.push(`/books/${accepted.book_id}`);
    } catch (err) {
      setRegenError(err instanceof Error ? err.message : "Failed to regenerate book");
      setRegenerating(false);
    }
  }, [bookResource.familyId, bookId, router]);

  const activeGenerating = book ? isBookGenerating(book.status) : false;

  const headerActions = book && book.status === "completed" && bookResource.familyId ? (
    <BookExportDownloads
      familyId={bookResource.familyId}
      bookId={book.book_id}
      availableFormats={book.available_formats}
    />
  ) : null;

  return (
    <div className="pb-16">
      <AppHeader
        title={book?.title ?? t("booksTitle")}
        fallbackHref="/books"
        actions={headerActions}
      />

      <ArchiveState
        resource={bookResource}
        loadingLabel={t("booksInProgress")}
      >
        {book && (
          <PageContainer>
            {/* Active generation state */}
            {activeGenerating ? (
              <div className="py-8">
                <div className="mb-6 text-center">
                  <h1 className="text-section font-bold text-ink">{book.title}</h1>
                  {book.subtitle && (
                    <p className="mt-1 text-body text-muted">{book.subtitle}</p>
                  )}
                </div>

                {bookResource.familyId && (
                  <BookProgressTracker
                    familyId={bookResource.familyId}
                    bookId={book.book_id}
                    initialProgress={book.progress}
                    onCompleted={() => {
                      bookResource.reload();
                    }}
                    onCancelled={() => {
                      bookResource.reload();
                    }}
                    onFailed={() => {
                      bookResource.reload();
                    }}
                  />
                )}
              </div>
            ) : book.status === "failed" || book.status === "cancelled" ? (
              /* Failed / Cancelled state */
              <div className="mx-auto max-w-lg py-12 text-center">
                <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
                  <AlertCircle className="size-7" />
                </div>
                <h1 className="text-section font-bold text-ink">
                  {book.status === "cancelled"
                    ? t("bookStageCancelled")
                    : t("bookStageFailed")}
                </h1>
                <p className="mt-2 text-body text-muted">
                  {book.error_code
                    ? `Код ошибки: ${book.error_code}`
                    : "Создание книги было прервано."}
                </p>

                {regenError && (
                  <div className="mt-4 rounded-xl bg-destructive/10 p-3 text-caption text-destructive">
                    {regenError}
                  </div>
                )}

                <div className="mt-6 flex items-center justify-center gap-4">
                  <Button asChild variant="ghost">
                    <Link href="/books">
                      <ArrowLeft className="size-4 mr-2" />
                      <span>{t("bookBackToList")}</span>
                    </Link>
                  </Button>

                  <Button
                    type="button"
                    variant="primary"
                    onClick={handleRegenerate}
                    disabled={regenerating}
                    className="gap-2"
                  >
                    <RefreshCw className={`size-4 ${regenerating ? "animate-spin" : ""}`} />
                    <span>{regenerating ? t("bookRegenerating") : t("bookRegenerateButton")}</span>
                  </Button>
                </div>
              </div>
            ) : book.status === "completed" ? (
              /* Completed book: metadata header + reader */
              <div>
                <div className="mb-6 rounded-2xl border border-ink/10 bg-raised p-6 shadow-xs">
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                    <div>
                      <h1 className="text-section sm:text-title font-bold text-ink leading-tight">
                        {book.title}
                      </h1>
                      {book.subtitle && (
                        <p className="mt-1 text-item text-muted">{book.subtitle}</p>
                      )}
                    </div>
                  </div>

                  {/* Grounded themes & anchor info */}
                  {(book.central_theme || book.narrative_voice || book.material_anchor) && (
                    <div className="mt-4 grid grid-cols-1 gap-2 border-t border-ink/[0.08] pt-4 text-caption text-ink/80 sm:grid-cols-3">
                      {book.central_theme && (
                        <div>
                          <span className="font-semibold text-muted block">
                            {t("bookDetailTheme")}
                          </span>
                          <span>{book.central_theme}</span>
                        </div>
                      )}
                      {book.narrative_voice && (
                        <div>
                          <span className="font-semibold text-muted block">
                            {t("bookDetailVoice")}
                          </span>
                          <span>{book.narrative_voice}</span>
                        </div>
                      )}
                      {book.material_anchor && (
                        <div>
                          <span className="font-semibold text-muted block">
                            {t("bookDetailAnchor")}
                          </span>
                          <span>{book.material_anchor}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Reader */}
                {bookResource.familyId && chaptersResource.data && (
                  <BookReader
                    familyId={bookResource.familyId}
                    bookId={book.book_id}
                    chapters={chaptersResource.data}
                  />
                )}
              </div>
            ) : null}
          </PageContainer>
        )}
      </ArchiveState>
    </div>
  );
}

export function BookDetailView({ bookId }: { bookId: string }) {
  return (
    <FamilyGate>
      <BookDetailContent bookId={bookId} />
    </FamilyGate>
  );
}

