"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, AlertCircle, ArrowRight, Clock, Users } from "lucide-react";
import { AuthFrame } from "@/components/auth/auth-frame";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import {
  acceptInvitation,
  previewInvitation,
  type FamilyInvitationPreview,
} from "@/lib/mura/core-api";
import { useMuraSession } from "@/lib/mura/session-provider";

interface PageProps {
  params: Promise<{ token: string }>;
}

export default function InvitePage({ params }: PageProps) {
  const { token } = use(params);
  const router = useRouter();
  const { t } = useMuraI18n();
  const { auth, selectFamily, refreshFamilies } = useMuraSession();

  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<FamilyInvitationPreview | null>(null);
  const [errorStatus, setErrorStatus] = useState<"expired" | "revoked" | "not_found" | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setErrorStatus(null);

    previewInvitation(token)
      .then((data) => {
        if (!active) return;
        if (data.status === "expired") {
          setErrorStatus("expired");
        } else if (data.status === "revoked") {
          setErrorStatus("revoked");
        } else {
          setPreview(data);
        }
      })
      .catch((err: { status?: number; api?: { code?: string } }) => {
        if (!active) return;
        const code = err.api?.code?.toLowerCase() || "";
        if (err.status === 410 || code.includes("expired")) {
          setErrorStatus("expired");
        } else if (code.includes("revoked")) {
          setErrorStatus("revoked");
        } else {
          setErrorStatus("not_found");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [token]);

  const handleAccept = async () => {
    if (accepting || !preview) return;
    setAccepting(true);
    setAcceptError(null);
    try {
      const result = await acceptInvitation(token);
      setAccepted(true);
      await refreshFamilies();
      selectFamily(result.family_id);
    } catch (err: unknown) {
      const apiErr = err as { api?: { message?: string } };
      setAcceptError(apiErr.api?.message || t("inviteInvalidBody"));
    } finally {
      setAccepting(false);
    }
  };

  const roleName = preview?.role === "editor" ? t("inviteRoleNameEditor") : t("inviteRoleNameViewer");

  return (
    <AuthFrame>
      <div className="rounded-card border border-ink/[0.08] bg-raised p-6 shadow-soft sm:p-8">
        {loading ? (
          <div className="space-y-4 py-8 text-center">
            <div className="mx-auto size-8 animate-spin rounded-full border-2 border-ink border-t-transparent" />
            <p className="text-body text-muted">{t("membersLoading")}</p>
          </div>
        ) : errorStatus === "expired" ? (
          <div className="space-y-4 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-sand text-ink">
              <Clock className="size-6" />
            </div>
            <h2 className="text-item font-bold text-ink">{t("inviteExpiredTitle")}</h2>
            <p className="text-body leading-relaxed text-muted">{t("inviteExpiredBody")}</p>
            <div className="pt-2">
              <Button asChild variant="soft" size="md" className="w-full">
                <Link href="/">{t("homeOpenTree")}</Link>
              </Button>
            </div>
          </div>
        ) : errorStatus === "revoked" ? (
          <div className="space-y-4 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-sand text-ink">
              <AlertCircle className="size-6" />
            </div>
            <h2 className="text-item font-bold text-ink">{t("inviteRevokedTitle")}</h2>
            <p className="text-body leading-relaxed text-muted">{t("inviteRevokedBody")}</p>
            <div className="pt-2">
              <Button asChild variant="soft" size="md" className="w-full">
                <Link href="/">{t("homeOpenTree")}</Link>
              </Button>
            </div>
          </div>
        ) : errorStatus === "not_found" || !preview ? (
          <div className="space-y-4 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-sand text-ink">
              <AlertCircle className="size-6" />
            </div>
            <h2 className="text-item font-bold text-ink">{t("inviteInvalidTitle")}</h2>
            <p className="text-body leading-relaxed text-muted">{t("inviteInvalidBody")}</p>
            <div className="pt-2">
              <Button asChild variant="soft" size="md" className="w-full">
                <Link href="/">{t("homeOpenTree")}</Link>
              </Button>
            </div>
          </div>
        ) : accepted ? (
          <div className="space-y-5 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-peach text-ink">
              <CheckCircle2 className="size-6" />
            </div>
            <h2 className="text-item font-bold text-ink">
              {t("inviteAcceptedSuccess", { family: preview.family_name })}
            </h2>
            <Button
              type="button"
              variant="primary"
              size="lg"
              onClick={() => router.push("/home")}
              className="w-full gap-2"
            >
              <span>{t("inviteGoToFamily")}</span>
              <ArrowRight className="size-4" />
            </Button>
          </div>
        ) : (
          <div className="space-y-6">
            <div className="space-y-3 text-center">
              <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-sand/60 text-ink">
                <Users className="size-6" />
              </div>
              <h2 className="text-item font-bold text-ink">{t("invitePreviewTitle")}</h2>
              <p className="text-body leading-relaxed text-ink/80">
                {t("invitePreviewBody", {
                  inviter: preview.inviter_name || t("memberUnnamed"),
                  family: preview.family_name,
                  role: roleName,
                })}
              </p>
            </div>

            <div className="rounded-control border border-ink/[0.06] bg-sand/30 p-3.5 text-center">
              <span className="text-meta text-muted">{t("inviteRoleLabel")}: </span>
              <span className="text-body font-semibold text-ink">{roleName}</span>
            </div>

            {acceptError && (
              <p className="rounded-control bg-danger/10 p-3 text-meta text-danger text-center">
                {acceptError}
              </p>
            )}

            {auth.status === "authenticated" ? (
              <Button
                type="button"
                variant="primary"
                size="lg"
                disabled={accepting}
                onClick={handleAccept}
                className="w-full"
              >
                {accepting ? t("inviteAccepting") : t("inviteAcceptButton")}
              </Button>
            ) : (
              <div className="space-y-3">
                <p className="text-center text-meta text-muted">{t("inviteSignInToAccept")}</p>
                <Button asChild variant="primary" size="lg" className="w-full">
                  <Link href={`/sign-in?redirect_url=/invite/${token}`}>
                    {t("landingSignIn")}
                  </Link>
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </AuthFrame>
  );
}
