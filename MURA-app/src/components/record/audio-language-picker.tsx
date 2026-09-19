"use client";

import { motion } from "framer-motion";
import { useMuraI18n, type TranslationKey } from "@/lib/i18n";
import { useAudioLanguage } from "@/lib/mura/audio-language";
import type { AudioLanguage } from "@/lib/language";
import { cn } from "@/lib/utils";

/**
 * What the speaker is about to speak, asked where it is actually decided.
 *
 * The preference already existed and already worked — it just lived in
 * Settings, two screens away from the moment anyone thinks about it. Someone
 * about to record their grandmother in Kazakh will not go looking for it, so in
 * practice every recording ran on the default and the live preview transcribed
 * Kazakh with a Russian recogniser.
 *
 * This steers the browser preview only. The recording still travels to Core as
 * a stated preference, and the archive transcript is produced by a recogniser
 * that is never given a language at all — so a wrong answer here costs an
 * approximate preview, never a wrong transcript.
 */

const OPTIONS: ReadonlyArray<{ value: AudioLanguage; labelKey: TranslationKey }> = [
  { value: "auto", labelKey: "audioLangShortAuto" },
  { value: "ru", labelKey: "audioLangShortRu" },
  { value: "kk", labelKey: "audioLangShortKk" },
  { value: "mixed", labelKey: "audioLangShortMixed" },
];

export function AudioLanguagePicker({ className }: { className?: string }) {
  const { t } = useMuraI18n();
  const { audioLanguage, setAudioLanguage } = useAudioLanguage();

  return (
    <div className={cn("flex flex-col items-center gap-3", className)}>
      <p className="text-meta text-muted">{t("recordLanguagePrompt")}</p>
      <div
        role="radiogroup"
        aria-label={t("recordLanguagePrompt")}
        className="grid w-full max-w-measure grid-cols-2 gap-1 rounded-[1.25rem] bg-sand/70 p-1 sm:w-auto sm:max-w-none sm:grid-cols-4 sm:rounded-full"
      >
        {OPTIONS.map((option) => {
          const active = audioLanguage === option.value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setAudioLanguage(option.value)}
              className={cn(
                "relative h-10 whitespace-nowrap rounded-full px-4 text-meta font-semibold transition-colors focus-ring",
                active ? "text-raised" : "text-ink/65 hover:text-ink",
              )}
            >
              {/*
                One shared layout id, so the pill travels between options
                instead of two of them cross-fading in place. The label sits
                above it and never moves, which keeps the row from reflowing.
              */}
              {active && (
                <motion.span
                  layoutId="audio-language-pill"
                  className="absolute inset-0 -z-10 rounded-full bg-ink-deep"
                  transition={{ type: "spring", stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative">{t(option.labelKey)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
