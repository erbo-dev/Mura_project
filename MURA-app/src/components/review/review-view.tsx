"use client";

import { HelpCircle } from "lucide-react";
import { useCallback } from "react";
import { ArchiveState } from "@/components/archive/archive-state";
import { FamilyGate } from "@/components/family/family-gate";
import { AppHeader } from "@/components/layout/app-header";
import { ConflictCard } from "@/components/review/conflict-card";
import { PageContainer } from "@/components/shell/page-container";
import { useMuraI18n } from "@/lib/i18n";
import { fetchArchiveReviewItems, type ArchiveReviewItem } from "@/lib/mura/archive-api";
import { fetchConflicts, type ConflictReview } from "@/lib/mura/conflict-api";
import { useMuraSession } from "@/lib/mura/session-provider";
import { useArchiveResource } from "@/lib/mura/use-archive";

interface ReviewData {
  questions: ArchiveReviewItem[];
  conflicts: ConflictReview[];
}

function ReviewContent() {
  const { t } = useMuraI18n();
  const { family, auth } = useMuraSession();
  const load = useCallback(async (familyId: string, signal: AbortSignal): Promise<ReviewData> => {
    const [items, conflicts] = await Promise.all([
      fetchArchiveReviewItems(familyId, signal), fetchConflicts(familyId, signal),
    ]);
    return { questions: items.filter((item) => item.kind === "open_question"), conflicts };
  }, []);
  const review = useArchiveResource<ReviewData>(load);
  const questions = review.data?.questions ?? [];
  const conflicts = review.data?.conflicts ?? [];
  const familyId = family.selectedFamilyId;
  const canDecide = family.selectedFamily?.capabilities.includes("resolve_conflicts") ?? false;
  const reviewerReference = auth.status === "authenticated" ? auth.user.userId : null;

  return (
    <div className="pb-16">
      <AppHeader title={t("reviewTitle")} fallbackHref="/home" />
      <ArchiveState
        resource={review}
        loadingLabel={t("reviewLoading")}
        isEmpty={questions.length === 0 && conflicts.length === 0}
        empty={
          <PageContainer>
            <div className="mx-auto max-w-[46ch] py-14 text-center">
              <h1 className="text-balance text-section font-bold leading-snug tracking-[-0.02em]">{t("reviewEmptyTitle")}</h1>
              <p className="mt-2.5 text-body leading-relaxed text-muted">{t("reviewEmptyBody")}</p>
            </div>
          </PageContainer>
        }
      >
        <PageContainer>
          <ul className="max-w-[58ch] divide-y divide-ink/[0.06] pt-1">
            {questions.map((entry) => (
              <li key={entry.review_id} className="flex items-start gap-3.5 py-4">
                <HelpCircle aria-hidden className="mt-0.5 size-5 shrink-0 text-ink/35" strokeWidth={1.8} />
                <div className="min-w-0">
                  <p className="text-item font-medium leading-snug">{entry.question}</p>
                  {entry.detail && <p className="mt-1.5 text-body leading-relaxed text-muted">{entry.detail}</p>}
                </div>
              </li>
            ))}
            {familyId && conflicts.map((conflict) => (
              <ConflictCard
                key={`${familyId}-${conflict.conflict_id}`}
                conflict={conflict}
                familyId={familyId}
                reviewerReference={reviewerReference}
                canDecide={canDecide}
                onChanged={review.reload}
              />
            ))}
          </ul>
        </PageContainer>
      </ArchiveState>
    </div>
  );
}

export function ReviewView() {
  return <FamilyGate><ReviewContent /></FamilyGate>;
}
