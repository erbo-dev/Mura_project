import { describe, expect, it } from "vitest";
import type { SavedMemory } from "@/lib/memory-store";
import { awaitsCoreResult, coreCompletion } from "@/lib/mura/reconcile-memory";

const memory = (patch: Partial<SavedMemory> = {}): SavedMemory => ({
  id: "local-1",
  createdAt: "2026-09-11T08:49:00.000Z",
  ui_language_at_creation: "ru",
  audio_language: "auto",
  detected_audio_language: "unknown",
  transcript_language: "unknown",
  output_language: "same_as_transcript",
  title: "Новая аудиозапись",
  summary: "",
  transcript: "",
  people: [],
  durationSec: 19,
  source: "mura_core",
  status: "processing",
  analyzed: false,
  recordingId: "rec_1",
  familyId: "family_1",
  ...patch,
});

describe("reconciling a memory Core has already finished", () => {
  it("knows an unanalysed entry is still waiting", () => {
    // The exact state a recording lands in when the app is closed during
    // processing: audio saved, placeholder title, no transcript.
    expect(awaitsCoreResult(memory())).toBe(true);
  });

  it("leaves a finished entry alone", () => {
    expect(awaitsCoreResult(memory({ analyzed: true }))).toBe(false);
  });

  it("still reconciles an entry that has a transcript but no analysis", () => {
    // A transcript alone is not the finished memory: the title, people and
    // events arrive with the analysis, and an entry stuck here shows a raw
    // transcript under a placeholder title.
    expect(awaitsCoreResult(memory({ transcript: "какой-то текст" }))).toBe(true);
  });

  it("cannot reconcile an entry with no way back to Core", () => {
    // Entries written before `recordingId` existed have no pointer to their
    // Core recording. Nothing can be fetched for them, and pretending
    // otherwise would mean guessing which recording they were.
    expect(awaitsCoreResult(memory({ recordingId: undefined }))).toBe(false);
    expect(awaitsCoreResult(memory({ familyId: undefined }))).toBe(false);
  });
});


describe("a finished recording that produced no story", () => {
  const result = (stories: unknown[]) => ({
    result: {
      transcript: { full_text: "Моя бабушка жила в Алматы." },
      extraction: {
        people_mentions: [{ mention_id: "m1", name: "Гульнара" }],
        events: [],
        stories,
      },
      resolutions: [],
    },
  });

  it("still keeps the transcript and the people", () => {
    // Seven people and a full transcript were discarded because the pipeline
    // wrote no story, and the screen then claimed the text was unrecognised.
    const patch = coreCompletion(result([]));
    expect(patch?.transcript).toBe("Моя бабушка жила в Алматы.");
    expect(patch?.people?.map((person) => person.name)).toEqual(["Гульнара"]);
    expect(patch?.status).toBe("completed");
    expect(patch?.analyzed).toBe(false);
  });

  it("does not erase the placeholder title with undefined", () => {
    const patch = coreCompletion(result([]));
    expect(patch && "title" in patch).toBe(false);
    expect(patch && "summary" in patch).toBe(false);
  });

  it("applies the story when there is one", () => {
    const patch = coreCompletion(result([{ title: "Лето у бабушки", summary: "Как мы ездили в Алматы." }]));
    expect(patch?.title).toBe("Лето у бабушки");
    expect(patch?.analyzed).toBe(true);
  });

  it("applies nothing before Core has a result", () => {
    expect(coreCompletion({ result: null })).toBeNull();
  });

  it("is not refetched on every visit once Core has answered", () => {
    expect(awaitsCoreResult(memory({ status: "completed", analyzed: false }))).toBe(false);
  });

  it("does reconcile an entry the browser wrongly marked failed", () => {
    // The browser wrote "failed" after one refused poll; that is not Core's verdict.
    expect(awaitsCoreResult(memory({ status: "failed" }))).toBe(true);
  });
});
