"use client";

import {
  ChevronLeft,
  ChevronRight,
  List,
  Loader2,
  Type,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import {
  fetchBookChapter,
  type BookChapter,
  type BookChapterSummary,
} from "@/lib/mura/book-api";

interface BookReaderProps {
  familyId: string;
  bookId: string;
  chapters: BookChapterSummary[];
  initialChapterNumber?: number;
}

export function BookReader({
  familyId,
  bookId,
  chapters,
  initialChapterNumber = 1,
}: BookReaderProps) {
  const { t } = useMuraI18n();

  const [currentChapterNum, setCurrentChapterNum] = useState(initialChapterNumber);
  const [currentChapter, setCurrentChapter] = useState<BookChapter | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Typography preferences
  const [isSerif, setIsSerif] = useState(true);
  const [fontSize, setFontSize] = useState<"md" | "lg" | "xl">("lg");
  const [isTocOpen, setIsTocOpen] = useState(false);

  // Load chapter text
  useEffect(() => {
    if (!familyId || !bookId || !currentChapterNum) return;

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetchBookChapter(familyId, bookId, currentChapterNum, controller.signal)
      .then((data) => {
        setCurrentChapter(data);
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Failed to load chapter");
          setCurrentChapter(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [familyId, bookId, currentChapterNum]);

  const goToPrev = useCallback(() => {
    if (currentChapterNum > 1) {
      setCurrentChapterNum((n) => n - 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [currentChapterNum]);

  const goToNext = useCallback(() => {
    if (currentChapterNum < chapters.length) {
      setCurrentChapterNum((n) => n + 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [currentChapterNum, chapters.length]);

  const selectChapter = useCallback((num: number) => {
    setCurrentChapterNum(num);
    setIsTocOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const textSizeClass =
    fontSize === "md"
      ? "text-body leading-relaxed"
      : fontSize === "lg"
      ? "text-[1.125rem] leading-[1.8]"
      : "text-[1.25rem] leading-[1.85]";

  const paragraphs = currentChapter?.text
    ? currentChapter.text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean)
    : [];

  return (
    <div className="relative mx-auto max-w-4xl pb-24">
      {/* Reader Toolbar */}
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-ink/10 bg-raised/95 px-4 py-2.5 backdrop-blur-md">
        <button
          type="button"
          onClick={() => setIsTocOpen((prev) => !prev)}
          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-body font-medium text-ink hover:bg-sand transition-colors"
          aria-expanded={isTocOpen}
        >
          <List className="size-4.5 text-ink/70" />
          <span>{t("bookReaderToc")}</span>
        </button>

        <div className="flex items-center gap-2">
          {/* Font Serif / Sans toggle */}
          <button
            type="button"
            onClick={() => setIsSerif((s) => !s)}
            title={isSerif ? "Switch to Sans-serif" : "Switch to Serif"}
            className="flex size-9 items-center justify-center rounded-lg text-ink/80 hover:bg-sand transition-colors"
          >
            <span className={isSerif ? "font-serif text-lg font-bold" : "font-sans text-sm font-bold"}>
              {isSerif ? "Aa" : "Aa"}
            </span>
          </button>

          {/* Font Size cycle */}
          <button
            type="button"
            onClick={() =>
              setFontSize((size) =>
                size === "md" ? "lg" : size === "lg" ? "xl" : "md",
              )
            }
            title="Adjust text size"
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-caption font-semibold text-ink/80 hover:bg-sand transition-colors"
          >
            <Type className="size-3.5" />
            <span className="uppercase">{fontSize}</span>
          </button>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-8 lg:grid-cols-[260px_1fr]">
        {/* Table of Contents Drawer/Sidebar */}
        <aside
          className={`${
            isTocOpen ? "block" : "hidden"
          } lg:block rounded-2xl border border-ink/10 bg-raised p-4 shadow-xs lg:sticky lg:top-16 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto`}
        >
          <h4 className="text-caption font-bold uppercase tracking-wider text-muted px-2 pb-2">
            {t("bookReaderToc")}
          </h4>
          <nav className="space-y-1">
            {chapters.map((ch) => {
              const active = ch.chapter_number === currentChapterNum;
              return (
                <button
                  key={ch.chapter_number}
                  type="button"
                  onClick={() => selectChapter(ch.chapter_number)}
                  className={`w-full text-left rounded-xl px-3 py-2 text-body transition-colors ${
                    active
                      ? "bg-ink font-semibold text-raised"
                      : "text-ink/80 hover:bg-sand"
                  }`}
                >
                  <span className="block text-caption font-medium opacity-80">
                    {t("bookReaderChapter", { number: ch.chapter_number })}
                  </span>
                  <span className="block truncate">
                    {ch.title || t("bookReaderChapter", { number: ch.chapter_number })}
                  </span>
                </button>
              );
            })}
          </nav>
        </aside>

        {/* Chapter Text Area */}
        <main className="min-w-0">
          {loading ? (
            <div className="flex min-h-[400px] items-center justify-center">
              <Loader2 className="size-7 animate-spin text-ink/40" />
            </div>
          ) : error ? (
            <div className="rounded-2xl bg-destructive/10 p-6 text-center text-body text-destructive">
              {error}
            </div>
          ) : currentChapter ? (
            <article
              className={`mx-auto max-w-reading rounded-3xl bg-raised p-6 shadow-soft sm:p-10 lg:p-12 ${
                isSerif ? "font-serif" : "font-sans"
              }`}
            >
              <header className="mb-8 border-b border-ink/10 pb-6 text-center">
                <span className="text-caption font-bold uppercase tracking-widest text-muted">
                  {t("bookReaderChapter", { number: currentChapter.chapter_number })}
                </span>
                <h1 className="mt-2 text-section sm:text-title font-bold text-ink leading-tight">
                  {currentChapter.title}
                </h1>
                {currentChapter.word_count > 0 && (
                  <span className="mt-2 block text-caption text-muted">
                    {t("booksCardWords", { count: currentChapter.word_count })}
                  </span>
                )}
              </header>

              <div className={`space-y-6 text-ink/90 ${textSizeClass}`}>
                {paragraphs.length > 0 ? (
                  paragraphs.map((para, i) => (
                    <p key={i} className="indent-6 sm:indent-8">
                      {para}
                    </p>
                  ))
                ) : (
                  <p className="text-center italic text-muted">
                    {t("bookReaderEmptyChapter")}
                  </p>
                )}
              </div>

              {/* Prev / Next chapter navigation */}
              <footer className="mt-12 flex items-center justify-between border-t border-ink/10 pt-6">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={goToPrev}
                  disabled={currentChapterNum <= 1}
                  className="gap-2"
                >
                  <ChevronLeft className="size-4" />
                  <span>{t("bookReaderPrevChapter")}</span>
                </Button>

                <span className="text-caption text-muted font-medium">
                  {currentChapterNum} / {chapters.length}
                </span>

                <Button
                  type="button"
                  variant="ghost"
                  onClick={goToNext}
                  disabled={currentChapterNum >= chapters.length}
                  className="gap-2"
                >
                  <span>{t("bookReaderNextChapter")}</span>
                  <ChevronRight className="size-4" />
                </Button>
              </footer>
            </article>
          ) : null}
        </main>
      </div>
    </div>
  );
}
