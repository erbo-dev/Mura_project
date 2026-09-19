import { completeMemoryFromCore, type SavedMemory } from "@/lib/memory-store";
import { fetchRecordingResult } from "@/lib/mura/core-api";
import { isPresentable, readAnalysis } from "@/lib/mura/pipeline-result";

/**
 * Bringing a local entry up to date with what Core finished.
 *
 * ## Why this exists apart from the processing screen
 *
 * Completion used to happen in exactly one place: the poll loop on
 * `/processing`. That screen tells people, correctly, that they may close the
 * app while a memory is being worked on — and if they did, nothing ever wrote
 * the result back. Core held a finished transcript, people and events, while
 * the browser went on showing «Новая аудиозапись» and «Текст не распознан»
 * forever. The pipeline was not broken; the last step simply never ran.
 *
 * So reconciliation has to work from a cold start, using only what the entry
 * itself carries. That is why `SavedMemory` records `recordingId` and
 * `familyId`.
 */

/**
 * The patch a finished Core result makes to a local entry, or null if Core has
 * not produced one yet.
 *
 * The transcript, people, events and places are applied whenever Core has
 * finished. Title and summary are applied only when the pipeline produced a
 * story. This used to be a single gate on "is there a story": a recording that
 * yielded seven people and a full transcript but no story was discarded whole,
 * and the screen said the text had not been recognised — which was false.
 *
 * Keys are omitted rather than set to `undefined`, because the merge is a
 * spread and an `undefined` title would erase the placeholder.
 */
export function coreCompletion(
  coreResult: unknown,
): (Partial<Omit<SavedMemory, "id" | "createdAt">> & { transcript: string }) | null {
  const analysis = readAnalysis(coreResult);
  if (!analysis.rawTranscript) return null;

  const told = isPresentable(analysis);
  return {
    ...(analysis.title ? { title: analysis.title } : {}),
    ...(analysis.summary ? { summary: analysis.summary } : {}),
    transcript: analysis.rawTranscript,
    ...(analysis.cleanTranscript ? { cleanTranscript: analysis.cleanTranscript } : {}),
    audio_language: analysis.languageContext.requested_audio_language,
    detected_audio_language: analysis.languageContext.detected_audio_language,
    transcript_language: analysis.languageContext.transcript_language,
    output_language: analysis.languageContext.requested_output_language,
    people: analysis.people.map((person) => ({
      name: person.name,
      relationship: person.relationToSpeaker ?? "",
      personId: person.personId ?? undefined,
      isNew: person.isNew,
    })),
    events: analysis.events.map((event) => ({
      title: event.title,
      description: event.description,
      dateText: event.dateText,
      location: event.location,
    })),
    places: analysis.places.map((place) => place.name),
    ...(analysis.evidenceQuotes && analysis.evidenceQuotes.length > 0
      ? { evidenceQuotes: analysis.evidenceQuotes }
      : {}),
    status: analysis.needsReview ? "needs_review" : "completed",
    analyzed: told,
  };
}

/** Write a finished Core result onto a local entry. */
export function applyCoreResult(memoryId: string, coreResult: unknown): boolean {
  const completion = coreCompletion(coreResult);
  if (!completion) return false;
  completeMemoryFromCore(memoryId, completion);
  return true;
}

/** Whether this entry is still waiting for a result Core may already hold. */
export function awaitsCoreResult(memory: SavedMemory): boolean {
  if (!memory.recordingId || !memory.familyId) return false;
  if (memory.analyzed === true) return false;
  // Core already answered: it finished, with or without a story. Asking again
  // would refetch the same result on every visit.
  //
  // A local "failed" is deliberately not in this list. The browser used to
  // write it after a single refused poll, and it is not Core's verdict.
  return memory.status !== "completed" && memory.status !== "needs_review";
}

/**
 * Fetch and apply the result for one entry, if there is one to apply.
 *
 * Silent on failure by design: a recording still being processed, an expired
 * session, or a network blip are all "not yet", and none of them should put an
 * error on a screen whose job is to show a memory.
 */
export async function reconcileMemory(memory: SavedMemory): Promise<boolean> {
  if (!awaitsCoreResult(memory)) return false;
  try {
    const result = await fetchRecordingResult(
      memory.recordingId as string,
      memory.familyId as string,
    );
    return applyCoreResult(memory.id, result);
  } catch {
    return false;
  }
}
