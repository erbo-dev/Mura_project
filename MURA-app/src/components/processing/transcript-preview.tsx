"use client";

import { motion } from "framer-motion";
import { useMemo } from "react";
import { useMuraI18n } from "@/lib/i18n";

const EASE = [0.23, 1, 0.32, 1] as const;

/** Splits on sentence ends while keeping the punctuation with its sentence. */
export function previewSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

/**
 * The speaker's own words, shown the moment recognition has them.
 *
 * Extraction takes most of a minute; recognition takes seconds. Waiting on a
 * spinner for the part that is already done made the whole thing feel slow.
 * The sentences arrive one after another rather than all at once, which reads
 * as the text being handed back rather than a block being pasted in.
 *
 * This is the recognised text, not the finished memory. The caption says that
 * people and stories are still being found, so nobody mistakes it for the end.
 */
export function TranscriptPreview({ text }: { text: string }) {
  const { t } = useMuraI18n();
  const sentences = useMemo(() => previewSentences(text), [text]);

  return (
    <motion.section
      aria-live="polite"
      className="mt-8 w-full"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3, ease: EASE }}
    >
      <p className="text-center text-meta text-muted">{t("processingPreviewCaption")}</p>
      <div
        className="mt-4 max-h-[38dvh] overflow-y-auto px-1"
        style={{
          maskImage: "linear-gradient(to bottom, black calc(100% - 32px), transparent)",
          WebkitMaskImage: "linear-gradient(to bottom, black calc(100% - 32px), transparent)",
        }}
      >
        <p className="text-reading leading-relaxed text-ink/85">
          {sentences.map((sentence, index) => (
            <motion.span
              key={index}
              className="inline"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45, delay: Math.min(index * 0.09, 1.2), ease: EASE }}
            >
              {sentence}{" "}
            </motion.span>
          ))}
        </p>
      </div>
    </motion.section>
  );
}
