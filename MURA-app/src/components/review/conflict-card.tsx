"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import { recordingAudioUrl } from "@/lib/mura/archive-api";
import { decideConflict, type ConflictClaim, type ConflictReview } from "@/lib/mura/conflict-api";
import { CoreRequestError } from "@/lib/mura/core-api";

function claimValue(claim: ConflictClaim): string {
  for (const key of ["value", "name", "relationship_type", "attribute_value", "text"]) {
    const value = claim.payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return claim.predicate;
}

export function ConflictCard({
  conflict,
  familyId,
  reviewerReference,
  canDecide,
  onChanged,
}: {
  conflict: ConflictReview;
  familyId: string;
  reviewerReference: string | null;
  canDecide: boolean;
  onChanged: () => void;
}) {
  const { t } = useMuraI18n();
  const [preferred, setPreferred] = useState(conflict.preferred_claim_id ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function decide(action: "resolve" | "dismiss" | "reopen") {
    if (!reviewerReference || !note.trim() || (action === "resolve" && !preferred)) return;
    setBusy(true);
    setFailure(null);
    try {
      await decideConflict(familyId, conflict.conflict_id, action, reviewerReference, note.trim(), preferred);
      setNote("");
      onChanged();
    } catch (error) {
      setFailure(error instanceof CoreRequestError ? error.api.message : t("reviewDecisionFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="border-b border-ink/[0.08] py-5">
      <p className="text-meta font-semibold text-ink/70">{t("reviewConflict")}</p>
      <h2 className="mt-1 text-item font-medium leading-snug">{conflict.rationale}</h2>
      <p className="mt-1 text-meta text-muted">
        {conflict.status === "open" ? t("reviewOpen") : conflict.status === "resolved" ? t("reviewResolved") : t("reviewDismissed")}
      </p>
      <fieldset className="mt-4 space-y-3" disabled={!canDecide || busy || conflict.status !== "open"}>
        <legend className="text-meta font-semibold">{t("reviewAlternatives")}</legend>
        {conflict.claims.map((claim) => (
          <label key={claim.claim_id} className="block rounded-xl border border-ink/10 p-3 text-body">
            <span className="flex items-start gap-3">
              {conflict.status === "open" && canDecide && (
                <input type="radio" name={`claim-${conflict.conflict_id}`} value={claim.claim_id} checked={preferred === claim.claim_id} onChange={() => setPreferred(claim.claim_id)} className="mt-1" />
              )}
              <span className="min-w-0 break-words">
                <span className="font-semibold">{claimValue(claim)}</span>
                {conflict.preferred_claim_id === claim.claim_id && <span className="ml-2 text-meta text-muted">({t("reviewPreviouslyPreferred")})</span>}
                <span className="mt-1 block text-meta text-muted">{t("reviewSource")}: {claim.recording_id}</span>
              </span>
            </span>
            {claim.evidence_quotes.length ? (
              <ul className="mt-2 space-y-1 border-l-2 border-ink/15 pl-3 text-meta text-muted">
                {claim.evidence_quotes.map((quote, index) => <li key={`${claim.claim_id}-${index}`} className="break-words">{quote}</li>)}
              </ul>
            ) : <p className="mt-2 text-meta text-muted">{t("reviewEvidenceUnavailable")}</p>}
            {claim.object_type === "story" && claim.source_object_id && (
              <Link href={`/story/${encodeURIComponent(claim.source_object_id)}`} className="mt-2 inline-block text-meta underline focus-ring">{t("reviewOpenStory")}</Link>
            )}
            <audio controls preload="none" src={recordingAudioUrl(familyId, claim.recording_id)} className="mt-2 w-full" aria-label={t("reviewSourceAudio")} />
          </label>
        ))}
      </fieldset>
      {conflict.resolution_note && <p className="mt-3 break-words text-meta text-muted">{t("reviewPreviousDecision")}: {conflict.resolution_note}</p>}
      {canDecide && reviewerReference && (
        <div className="mt-4 space-y-3">
          <label className="block text-meta font-semibold" htmlFor={`note-${conflict.conflict_id}`}>{t("reviewReason")}</label>
          <textarea id={`note-${conflict.conflict_id}`} value={note} maxLength={4000} onChange={(event) => setNote(event.target.value)} className="min-h-20 w-full rounded-xl border border-ink/20 bg-raised p-3 text-body focus-ring" />
          <div className="flex flex-wrap gap-2">
            {conflict.status === "open" ? <>
              <Button type="button" size="md" disabled={busy || !preferred || !note.trim()} onClick={() => void decide("resolve")}>{t("reviewPreferClaim")}</Button>
              <Button type="button" size="md" variant="soft" disabled={busy || !note.trim()} onClick={() => void decide("dismiss")}>{t("reviewDismiss")}</Button>
            </> : <Button type="button" size="md" variant="soft" disabled={busy || !note.trim()} onClick={() => void decide("reopen")}>{t("reviewReopen")}</Button>}
          </div>
        </div>
      )}
      {failure && <p role="alert" className="mt-2 text-meta text-danger">{failure}</p>}
    </li>
  );
}
