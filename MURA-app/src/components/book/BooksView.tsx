"use client";

import { BookPlus, BookOpen, Clock, Layers } from "lucide-react";
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
  fetchBooks,
  type BookAccepted,
  type BookListPage,
  type BookSummary,
} from "@/lib/mura/book-api";
import { getBookStatusInfo } from "@/lib/mura/book-progress";
import { useArchiveResource } from "@/lib/mura/use-archive";
import { BookCreateModal } from "./BookCreateModal";

function BooksContent() {
  const { t } = useMuraI18n();
  const router = useRouter();

  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const loadBooks = useCallback(
    async (familyId: string, signal: AbortSignal): Promise<BookSummary[]> => {
      const resp: BookListPage = await fetchBooks(familyId, 50, 0, signal);
      return resp.items;
    },
    [],
  );

  const booksResource = useArchiveResource<BookSummary[]>(loadBooks);
  const books = booksResource.data ?? [];

  const handleCreated = useCallback(
    (accepted: BookAccepted) => {
      booksResource.reload();
      router.push(`/books/${accepted.book_id}`);
    },
    [booksResource, router],
  );

  const createButton = (
    <Button
      type="button"
      variant="primary"
      size="md"
      onClick={() => setIsCreateOpen(true)}
      className="gap-2"
    >
      <BookPlus className="size-4" />
      <span>{t("booksCreateButton")}</span>
    </Button>
  );

  return (
    <div className="pb-16">
      <AppHeader
        title={t("booksTitle")}
        fallbackHref="/home"
        actions={createButton}
      />

      <ArchiveState
        resource={booksResource}
        loadingLabel={t("booksInProgress")}
        isEmpty={books.length === 0}
        empty={
          <PageContainer>
            <div className="mx-auto max-w-measure py-14 text-center">
              <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-sand text-ink">
                <BookOpen className="size-7 text-ink/70" />
              </div>
              <h1 className="text-balance text-section font-bold leading-snug tracking-[-0.02em] text-ink">
                {t("booksEmptyTitle")}
              </h1>
              <p className="mt-2.5 text-body leading-relaxed text-muted">
                {t("booksEmptyBody")}
              </p>
              <div className="mt-6 flex justify-center">
                {createButton}
              </div>
            </div>
          </PageContainer>
        }
      >
        <PageContainer>
          <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h2 className="text-section font-bold text-ink">
                {t("booksTitle")}
              </h2>
              <p className="mt-1 text-meta text-muted">
                {t("booksSubtitle")}
              </p>
            </div>
            <div className="hidden sm:block">
              {createButton}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {books.map((book) => {
              const statusInfo = getBookStatusInfo(book.status);
              const formattedDate = new Date(book.created_at).toLocaleDateString();

              let badgeColor = "bg-ink/10 text-muted";
              if (statusInfo.variant === "default") badgeColor = "bg-ink text-raised";
              else if (statusInfo.variant === "amber") badgeColor = "bg-sand text-ink border border-ink/20";
              else if (statusInfo.variant === "destructive") badgeColor = "bg-destructive/10 text-destructive";

              return (
                <Link
                  key={book.book_id}
                  href={`/books/${book.book_id}`}
                  className="group flex flex-col justify-between rounded-2xl border border-ink/10 bg-raised p-5 shadow-soft transition-all hover:border-ink/25 hover:shadow-md"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="rounded-md bg-sand px-2 py-0.5 text-caption font-bold uppercase tracking-wider text-ink/70">
                        {book.output_language.toUpperCase()}
                      </span>
                      <span className={`rounded-full px-2.5 py-0.5 text-caption font-medium ${badgeColor}`}>
                        {t(statusInfo.labelKey)}
                      </span>
                    </div>

                    <h3 className="mt-3 text-item font-bold leading-snug text-ink group-hover:text-ink/80 transition-colors">
                      {book.title}
                    </h3>
                    {book.subtitle && (
                      <p className="mt-1 text-caption text-muted line-clamp-2">
                        {book.subtitle}
                      </p>
                    )}
                  </div>

                  <div className="mt-5 border-t border-ink/[0.06] pt-3 text-caption text-muted space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5">
                        <Layers className="size-3.5 text-muted" />
                        {t("booksCardChapters", {
                          approved: book.chapters_approved,
                          total: book.chapters_total,
                        })}
                      </span>
                      {book.word_count > 0 && (
                        <span>
                          {t("booksCardWords", { count: book.word_count })}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 text-muted">
                      <Clock className="size-3.5" />
                      <span>{t("booksCardDate", { date: formattedDate })}</span>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        </PageContainer>
      </ArchiveState>

      {booksResource.familyId && (
        <BookCreateModal
          isOpen={isCreateOpen}
          onClose={() => setIsCreateOpen(false)}
          familyId={booksResource.familyId}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

export function BooksView() {
  return (
    <FamilyGate>
      <BooksContent />
    </FamilyGate>
  );
}
