import { coreRequest } from "@/lib/mura/core-api";

export interface ConflictClaim {
  claim_id: string;
  recording_id: string;
  source_object_id: string;
  object_type: string;
  predicate: string;
  status: string;
  verification_status: string;
  evidence_ids: string[];
  evidence_quotes: string[];
  payload: Record<string, unknown>;
}

export interface ConflictDecision {
  action: "resolve" | "dismiss" | "reopen" | "auto_reopen";
  note: string;
  preferred_claim_id: string | null;
  created_at: string;
}

export interface ConflictReview {
  conflict_id: string;
  family_id: string;
  status: "open" | "resolved" | "dismissed";
  rationale: string;
  preferred_claim_id: string | null;
  resolution_note: string | null;
  claims: ConflictClaim[];
  decisions: ConflictDecision[];
}

const scope = (familyId: string, conflictId?: string) =>
  `/v1/families/${encodeURIComponent(familyId)}/conflicts${conflictId ? `/${encodeURIComponent(conflictId)}` : ""}`;

export function fetchConflicts(familyId: string, signal?: AbortSignal): Promise<ConflictReview[]> {
  return coreRequest<ConflictReview[]>(scope(familyId), { signal });
}

export function decideConflict(
  familyId: string,
  conflictId: string,
  action: "resolve" | "dismiss" | "reopen",
  reviewerReference: string,
  note: string,
  preferredClaimId?: string,
): Promise<{ conflict: ConflictReview }> {
  return coreRequest<{ conflict: ConflictReview }>(`${scope(familyId, conflictId)}/${action}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      reviewer_reference: reviewerReference,
      note,
      ...(action === "resolve" ? { preferred_claim_id: preferredClaimId } : {}),
    }),
  });
}
