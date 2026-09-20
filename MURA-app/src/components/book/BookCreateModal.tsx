"use client";

import { Loader2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useMuraI18n, type Locale } from "@/lib/i18n";
import {
  createBook,
  fetchBookSources,
  type BookAccepted,
  type BookSourceOption,
} from "@/lib/mura/book-api";

interface BookCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  familyId: string;
  onCreated: (accepted: BookAccepted) => void;
}

export const PRESET_WORD_COUNTS = [
  { words: 20000, labelKey: "bookWordCountPresetShort" as const },
  { words: 25000, labelKey: "bookWordCountPresetMedium" as const },
  { words: 30000, labelKey: "bookWordCountPresetLong" as const },
];

export function BookCreateModal({
  isOpen,
  onClose,
  familyId,
  onCreated,
}: BookCreateModalProps) {
  const { t, locale } = useMuraI18n();

  const [title, setTitle] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [outputLanguage, setOutputLanguage] = useState<Locale>(locale);
  const [targetWordCount, setTargetWordCount] = useState<number>(25000);
  const [sources, setSources] = useState<BookSourceOption[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync default language with current locale when modal opens
  useEffect(() => {
    if (isOpen) {
      setOutputLanguage(locale);
      setTitle("");
      setSubtitle("");
      setError(null);
    }
  }, [isOpen, locale]);

  // Load eligible archive sources
  useEffect(() => {
    if (!isOpen || !familyId) return;

    const controller = new AbortController();
    setLoadingSources(true);

    fetchBookSources(familyId, controller.signal)
      .then((items) => {
        setSources(items);
        // By default select all eligible sources
        setSelectedIds(items.map((s) => s.recording_id));
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Failed to load sources");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingSources(false);
        }
      });

    return () => controller.abort();
  }, [isOpen, familyId]);

  // Close on escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const toggleSource = useCallback((id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(sources.map((s) => s.recording_id));
  }, [sources]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      const accepted = await createBook(familyId, {
        title: title.trim(),
        subtitle: subtitle.trim() || null,
        output_language: outputLanguage,
        target_word_count: targetWordCount,
        requested_recording_ids: selectedIds.length > 0 ? selectedIds : undefined,
      });
      onCreated(accepted);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create book");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-ink/40 p-4 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="relative my-8 w-full max-w-xl rounded-3xl bg-raised p-6 shadow-soft sm:p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-5 top-5 flex size-9 items-center justify-center rounded-full text-muted hover:bg-sand hover:text-ink focus-ring"
        >
          <X className="size-5" />
        </button>

        <h2 className="text-section font-bold tracking-tight text-ink">
          {t("bookCreateTitle")}
        </h2>
        <p className="mt-1 text-meta text-muted">{t("bookCreateDesc")}</p>

        {error && (
          <div className="mt-4 rounded-xl bg-destructive/10 p-3 text-body font-medium text-destructive">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-6 space-y-5">
          {/* Title */}
          <div>
            <label className="block text-meta font-medium text-ink/80">
              {t("bookTitleLabel")} *
            </label>
            <input
              type="text"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("bookTitlePlaceholder")}
              className="mt-1.5 w-full rounded-xl border border-ink/15 bg-background px-4 py-2.5 text-body text-ink placeholder:text-muted focus:border-ink focus:outline-none focus:ring-1 focus:ring-ink"
            />
          </div>

          {/* Subtitle */}
          <div>
            <label className="block text-meta font-medium text-ink/80">
              {t("bookSubtitleLabel")}
            </label>
            <input
              type="text"
              value={subtitle}
              onChange={(e) => setSubtitle(e.target.value)}
              placeholder={t("bookSubtitlePlaceholder")}
              className="mt-1.5 w-full rounded-xl border border-ink/15 bg-background px-4 py-2.5 text-body text-ink placeholder:text-muted focus:border-ink focus:outline-none focus:ring-1 focus:ring-ink"
            />
          </div>

          {/* Language Selector */}
          <div>
            <label className="block text-meta font-medium text-ink/80">
              {t("bookLanguageLabel")}
            </label>
            <div className="mt-1.5 grid grid-cols-3 gap-2">
              {(["ru", "kk", "en"] as const).map((lang) => (
                <button
                  key={lang}
                  type="button"
                  onClick={() => setOutputLanguage(lang)}
                  className={`flex h-11 items-center justify-center rounded-xl border text-body font-medium transition-colors ${
                    outputLanguage === lang
                      ? "border-ink bg-ink text-raised shadow-xs"
                      : "border-ink/15 bg-background text-ink/80 hover:bg-sand"
                  }`}
                >
                  {lang === "ru" ? "Русский" : lang === "kk" ? "Қазақша" : "English"}
                </button>
              ))}
            </div>
          </div>

          {/* Word Count Presets */}
          <div>
            <label className="block text-meta font-medium text-ink/80">
              {t("bookWordCountLabel")}
            </label>
            <div className="mt-1.5 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {PRESET_WORD_COUNTS.map((preset) => (
                <button
                  key={preset.words}
                  type="button"
                  onClick={() => setTargetWordCount(preset.words)}
                  className={`flex h-11 items-center justify-center rounded-xl border px-3 text-caption font-medium transition-colors ${
                    targetWordCount === preset.words
                      ? "border-ink bg-ink text-raised shadow-xs"
                      : "border-ink/15 bg-background text-ink/80 hover:bg-sand"
                  }`}
                >
                  {t(preset.labelKey)}
                </button>
              ))}
            </div>
          </div>

          {/* Source Memories Picker */}
          <div>
            <div className="flex items-center justify-between">
              <label className="block text-meta font-medium text-ink/80">
                {t("bookSourcesLabel")}
              </label>
              {sources.length > 0 && (
                <button
                  type="button"
                  onClick={selectAll}
                  className="text-caption font-medium text-ink/60 hover:text-ink underline"
                >
                  {t("bookSourcesSelectAll", { count: sources.length })}
                </button>
              )}
            </div>
            <p className="mt-0.5 text-caption text-muted">{t("bookSourcesHint")}</p>

            <div className="mt-2 max-h-44 overflow-y-auto rounded-xl border border-ink/15 bg-background p-2 divide-y divide-ink/[0.06]">
              {loadingSources ? (
                <div className="flex items-center justify-center py-6 text-muted">
                  <Loader2 className="size-5 animate-spin" />
                </div>
              ) : sources.length === 0 ? (
                <div className="py-4 text-center text-meta text-muted">
                  {t("bookSourcesNoEligible")}
                </div>
              ) : (
                sources.map((src) => {
                  const isChecked = selectedIds.includes(src.recording_id);
                  return (
                    <label
                      key={src.recording_id}
                      className="flex cursor-pointer items-center justify-between gap-3 py-2 px-2 hover:bg-sand/60 rounded-lg transition-colors"
                    >
                      <div className="min-w-0 flex-1">
                        <span className="block truncate text-body font-medium text-ink">
                          {src.speaker_name || src.title || "Воспоминание"}
                        </span>
                        <span className="block text-caption text-muted">
                          {src.story_count} историй · {src.person_count} персон
                        </span>
                      </div>
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleSource(src.recording_id)}
                        className="size-4.5 rounded border-ink/30 text-ink focus:ring-ink"
                      />
                    </label>
                  );
                })
              )}
            </div>
          </div>

          {/* Submit */}
          <div className="flex items-center justify-end gap-3 pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("cancelButton")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={submitting || !title.trim() || (sources.length > 0 && selectedIds.length === 0)}
              className="gap-2"
            >
              {submitting && <Loader2 className="size-4 animate-spin" />}
              <span>{submitting ? t("bookCreating") : t("bookSubmitCreate")}</span>
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
