"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AudioLanguage } from "@/lib/language";

export interface LiveSentence {
  text: string;
  /** False only for the phrase still being spoken. */
  complete: boolean;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly 0: { readonly transcript: string };
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

export interface UseLiveTranscriptOptions {
  listening: boolean;
  audioLanguage: AudioLanguage;
}

/**
 * Which language the browser recogniser previews in.
 *
 * Web Speech takes exactly one language and has no RU+KK mode, so a
 * code-switched sentence cannot be previewed faithfully by it at all. That is a
 * limit of this preview, not of MURA: the archive transcript comes from Whisper
 * with no language pinned, and nothing shown here is ever sent to the server.
 *
 * AUTO previews in Russian rather than showing nothing. Russian is the majority
 * language of these recordings and shares the Cyrillic script with Kazakh, so a
 * speaker sees their words appear as they talk and Kazakh stretches come back
 * approximate. Showing nothing at all was the worse failure: the screen sat
 * silent while someone spoke into it, which reads as a microphone that is not
 * working.
 *
 * The approximation is why the caption under the preview says plainly that the
 * exact text arrives after processing.
 */
export function liveRecognitionLanguageOptions(
  audioLanguage: AudioLanguage,
): readonly string[] {
  if (audioLanguage === "kk") return ["kk-KZ", "kk", "ru-RU"];
  return ["ru-RU", "ru"];
}

/** Uses the browser speech recognizer only as a non-authoritative preview. */
export function useLiveTranscript({
  listening,
  audioLanguage,
}: UseLiveTranscriptOptions) {
  const [finalPhrases, setFinalPhrases] = useState<string[]>([]);
  const [interimPhrase, setInterimPhrase] = useState("");
  const [supported, setSupported] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const listeningRef = useRef(listening);
  const restartTimerRef = useRef<number | null>(null);

  useEffect(() => {
    listeningRef.current = listening;
  }, [listening]);

  useEffect(() => {
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    const languageOptions = liveRecognitionLanguageOptions(audioLanguage);
    const previewSupported = Boolean(Recognition) && languageOptions.length > 0;
    setSupported(previewSupported);
    if (!Recognition || languageOptions.length === 0) return;

    const recognition = new Recognition();
    let languageIndex = 0;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.lang = languageOptions[languageIndex];
    recognition.onresult = (event) => {
      const completed: string[] = [];
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result[0]?.transcript.trim();
        if (!text) continue;
        if (result.isFinal) completed.push(text);
        else interim = `${interim} ${text}`.trim();
      }
      if (completed.length > 0) {
        setFinalPhrases((current) => [...current, ...completed]);
      }
      setInterimPhrase(interim);
      setError(null);
    };
    recognition.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      if (event.error === "language-not-supported" && languageIndex < languageOptions.length - 1) {
        languageIndex += 1;
        recognition.lang = languageOptions[languageIndex];
        setError(null);
        return;
      }
      setError(event.error);
    };
    recognition.onend = () => {
      if (!listeningRef.current) return;
      restartTimerRef.current = window.setTimeout(() => {
        try {
          recognition.start();
        } catch {
          // The browser may still be closing the previous recognition session.
        }
      }, 250);
    };
    recognitionRef.current = recognition;
    if (listeningRef.current) {
      try {
        recognition.start();
      } catch {
        // A previous browser session may still be closing.
      }
    }

    return () => {
      if (restartTimerRef.current !== null) window.clearTimeout(restartTimerRef.current);
      recognition.onend = null;
      recognition.abort();
      recognitionRef.current = null;
    };
  }, [audioLanguage]);

  useEffect(() => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    if (listening) {
      try {
        recognition.start();
      } catch {
        // start() throws when a session is already active; that session is usable.
      }
    } else {
      recognition.stop();
      setInterimPhrase("");
    }
  }, [listening]);

  const reset = useCallback(() => {
    setFinalPhrases([]);
    setInterimPhrase("");
    setError(null);
  }, []);

  const sentences: LiveSentence[] = [
    ...finalPhrases.map((text) => ({ text, complete: true })),
    ...(interimPhrase ? [{ text: interimPhrase, complete: false }] : []),
  ];

  return {
    sentences,
    reset,
    supported,
    error,
    previewMode:
      audioLanguage === "auto" || audioLanguage === "mixed"
        ? ("disabled_for_multilingual" as const)
        : ("explicit_language" as const),
  };
}
