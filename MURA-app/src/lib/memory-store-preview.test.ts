import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  getSavedMemory,
  previewMemoryTranscriptFromCore,
  setMemoryOwner,
  type SavedMemory,
} from "@/lib/memory-store";

const OWNER = "user_preview";
const KEY = `mura-saved-memories-v1::${OWNER}`;

function store(): Map<string, string> {
  const values = new Map<string, string>();
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => void values.set(key, value),
      removeItem: (key: string) => void values.delete(key),
    },
  };
  return values;
}

const memory = (transcript: string): SavedMemory => ({
  id: "local-1",
  createdAt: "2026-09-13T02:21:00.000Z",
  ui_language_at_creation: "ru",
  audio_language: "auto",
  detected_audio_language: "unknown",
  transcript_language: "unknown",
  output_language: "same_as_transcript",
  title: "Новая аудиозапись",
  summary: "",
  transcript,
  people: [],
  durationSec: 75,
  source: "mura_core",
  status: "processing",
  analyzed: false,
  recordingId: "rec_1",
  familyId: "family_1",
});

describe("recognised text before the finished result", () => {
  let values: Map<string, string>;

  beforeEach(() => {
    values = store();
    setMemoryOwner(OWNER);
  });

  afterEach(() => {
    setMemoryOwner(null);
    delete (globalThis as { window?: unknown }).window;
  });

  it("fills an empty transcript so a closed app still shows the words", () => {
    values.set(KEY, JSON.stringify([memory("")]));

    previewMemoryTranscriptFromCore("local-1", "Бабушка жила в Алматы.");

    expect(getSavedMemory("local-1")?.transcript).toBe("Бабушка жила в Алматы.");
  });

  it("never replaces text that is already there", () => {
    // The invariant this path must not weaken: nothing may overwrite what the
    // user actually said.
    values.set(KEY, JSON.stringify([memory("Уже сохранённый текст.")]));

    previewMemoryTranscriptFromCore("local-1", "Другой текст.");

    expect(getSavedMemory("local-1")?.transcript).toBe("Уже сохранённый текст.");
  });

  it("ignores empty text", () => {
    values.set(KEY, JSON.stringify([memory("")]));

    previewMemoryTranscriptFromCore("local-1", "   ");

    expect(getSavedMemory("local-1")?.transcript).toBe("");
  });

  it("does not mark the memory analysed or finished", () => {
    // Showing recognised text must not end the wait for people and stories.
    values.set(KEY, JSON.stringify([memory("")]));

    previewMemoryTranscriptFromCore("local-1", "Бабушка жила в Алматы.");

    const saved = getSavedMemory("local-1");
    expect(saved?.analyzed).toBe(false);
    expect(saved?.status).toBe("processing");
  });
});
