"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useClerkSignOut } from "@/lib/auth/providers/clerk/use-clerk-sign-out";
import { notifyDevSessionChanged } from "@/lib/auth/providers/dev/use-dev-session";
import { useMuraI18n } from "@/lib/i18n";
import { getMemoryOwner, purgeLocalRecordingsFor, setMemoryOwner } from "@/lib/memory-store";
import { CoreRequestError, deleteAccount, type AccountDeletionResult } from "@/lib/mura/core-api";
import { SELECTED_FAMILY_STORAGE_KEY } from "@/lib/mura/family-session";

type Provider = "clerk" | "supabase" | "dev";

async function signOutCookieProvider(provider: "supabase" | "dev") {
  const response = await fetch(
    provider === "supabase" ? "/api/auth/sign-out" : "/api/dev-auth/session",
    { method: provider === "supabase" ? "POST" : "DELETE", cache: "no-store" },
  );
  if (!response.ok) throw new Error("provider sign-out failed");
  if (provider === "dev") notifyDevSessionChanged();
  window.location.assign("/");
}

export function DeleteAccountControl({ provider }: { provider: Provider }) {
  if (provider === "clerk") return <ClerkDeleteAccount />;
  return <DeleteAccountForm signOut={() => signOutCookieProvider(provider)} />;
}

function ClerkDeleteAccount() {
  const signOut = useClerkSignOut();
  return <DeleteAccountForm signOut={signOut} />;
}

function DeleteAccountForm({ signOut }: { signOut: () => Promise<unknown> }) {
  const { t } = useMuraI18n();
  const [expanded, setExpanded] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleted, setDeleted] = useState<AccountDeletionResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function endSession(clearError = true) {
    setBusy(true);
    if (clearError) setFailure(null);
    try {
      await signOut();
    } catch {
      setFailure(t("accountDeleteSignOutFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (busy || confirmation !== t("accountDeleteConfirmWord")) return;
    setBusy(true);
    setFailure(null);
    let result: AccountDeletionResult;
    try {
      result = await deleteAccount();
      if (!result.mura_data_deleted) throw new Error("Core did not confirm account deletion");
    } catch (error) {
      setFailure(
        error instanceof CoreRequestError && error.api.code === "account_deletion_requires_owner_transfer"
          ? t("accountDeleteOwnerBlock")
          : t("accountDeleteFailed"),
      );
      setBusy(false);
      return;
    }
    setDeleted(result);
    const departing = getMemoryOwner();
    setMemoryOwner(null);
    try {
      await purgeLocalRecordingsFor(departing);
      window.localStorage.removeItem(SELECTED_FAMILY_STORAGE_KEY);
    } catch {
      setFailure(t("accountDeleteLocalCleanupFailed"));
    }
    await endSession(false);
    setBusy(false);
  }

  return (
    <div className="mt-6 border-t border-ink/10 pt-5">
      {!expanded ? (
        <Button type="button" variant="ghost" onClick={() => setExpanded(true)} className="text-danger">
          {t("accountDeleteOpen")}
        </Button>
      ) : (
        <div className="space-y-3 text-body">
          <p className="font-semibold">{t("accountDeleteTitle")}</p>
          <p className="max-w-measure text-meta leading-relaxed text-muted">{t("accountDeleteExplanation")}</p>
          <p className="max-w-measure text-meta leading-relaxed text-muted">{t("accountDeleteOwnerBlock")}</p>
          <p className="max-w-measure text-meta leading-relaxed text-muted">{t("accountDeleteProviderNote")}</p>
          {deleted ? (
            <>
              <p role="status" className="text-meta">{deleted.identity_provider_account_deleted ? t("accountDeleteProviderDeleted") : t("accountDeleteProviderStillExists")}</p>
              <Button type="button" variant="soft" disabled={busy} onClick={() => void endSession()}>{t("accountDeleteSignOut")}</Button>
            </>
          ) : (
            <>
              <label className="block text-meta" htmlFor="delete-account-confirmation">{t("accountDeleteConfirmPrompt")}</label>
              <input id="delete-account-confirmation" value={confirmation} autoComplete="off" onChange={(event) => setConfirmation(event.target.value)} className="h-11 w-full max-w-xs rounded-xl border border-ink/20 bg-raised px-3 text-body focus-ring" />
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="danger" disabled={busy || confirmation !== t("accountDeleteConfirmWord")} onClick={() => void confirm()}>{t("accountDeleteConfirm")}</Button>
                <Button type="button" variant="ghost" disabled={busy} onClick={() => { setExpanded(false); setConfirmation(""); setFailure(null); }}>{t("accountDeleteCancel")}</Button>
              </div>
            </>
          )}
          {failure && <p role="alert" className="text-meta text-danger">{failure}</p>}
        </div>
      )}
    </div>
  );
}
