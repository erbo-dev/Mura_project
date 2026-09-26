"use client";

/**
 * Who else is in this family archive, and inviting new members.
 *
 * An owner manages members and invitations. Readers and editors see who else
 * belongs to the family archive, but invitation controls are reserved for the owner.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, UserPlus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { useMuraI18n } from "@/lib/i18n";
import {
  createFamilyInvitation,
  fetchFamilyMembers,
  listFamilyInvitations,
  revokeFamilyInvitation,
  type FamilyInvitationView,
  type FamilyRole,
  type MemberView,
} from "@/lib/mura/core-api";
import { useArchiveResource } from "@/lib/mura/use-archive";
import { useMuraSession } from "@/lib/mura/session-provider";

const ROLE_KEY: Record<FamilyRole, "roleOwner" | "roleEditor" | "roleViewer"> = {
  owner: "roleOwner",
  editor: "roleEditor",
  viewer: "roleViewer",
};

export function MembersSection() {
  const { t, locale } = useMuraI18n();
  const { auth, family } = useMuraSession();
  const loadMembers = useCallback(
    (familyId: string, signal: AbortSignal) => fetchFamilyMembers(familyId, signal),
    [],
  );
  const members = useArchiveResource<MemberView[]>(loadMembers);

  const selectedFamily = family.selectedFamily;
  const familyId = selectedFamily?.family_id ?? null;
  const canManageMembers =
    selectedFamily?.role === "owner" ||
    Boolean(selectedFamily?.capabilities?.includes("manage_members"));

  const [invitations, setInvitations] = useState<FamilyInvitationView[]>([]);
  const [loadingInvitations, setLoadingInvitations] = useState(false);
  const [inviteRole, setInviteRole] = useState<"editor" | "viewer">("viewer");
  const [creating, setCreating] = useState(false);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshInvitations = useCallback(async () => {
    if (!familyId || !canManageMembers) return;
    setLoadingInvitations(true);
    try {
      const list = await listFamilyInvitations(familyId, "pending");
      setInvitations(list);
    } catch {
      // Non-blocking: if pending list fails to load, preserve current UI
    } finally {
      setLoadingInvitations(false);
    }
  }, [familyId, canManageMembers]);

  useEffect(() => {
    if (canManageMembers && familyId) {
      refreshInvitations();
    }
  }, [canManageMembers, familyId, refreshInvitations]);

  const handleCreateInvite = async () => {
    if (!familyId) return;
    setCreating(true);
    setError(null);
    setCreatedUrl(null);
    setCopied(false);
    try {
      const inv = await createFamilyInvitation(familyId, inviteRole);
      const fullUrl =
        typeof window !== "undefined"
          ? `${window.location.origin}${inv.invitation_url}`
          : inv.invitation_url;
      setCreatedUrl(fullUrl);
      await refreshInvitations();
    } catch {
      setError(t("inviteErrorCreate"));
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!createdUrl) return;
    try {
      await navigator.clipboard.writeText(createdUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard fallback
    }
  };

  const handleRevoke = async (invitationId: string) => {
    if (!familyId) return;
    setRevokingId(invitationId);
    setError(null);
    try {
      await revokeFamilyInvitation(familyId, invitationId);
      await refreshInvitations();
    } catch {
      setError(t("inviteErrorRevoke"));
    } finally {
      setRevokingId(null);
    }
  };

  if (members.status === "loading") {
    return <p className="text-meta text-muted">{t("membersLoading")}</p>;
  }
  if (members.status === "forbidden") {
    return <p className="text-meta text-muted">{t("membersForbidden")}</p>;
  }
  if (!members.data) return null;

  const me = auth.status === "authenticated" ? auth.user.userId : null;

  return (
    <div className="space-y-6">
      {/* Existing members */}
      <ul className="space-y-2.5">
        {members.data.map((member) => (
          <li key={member.user_id} className="flex items-center gap-3">
            <PersonAvatar
              personId={member.user_id}
              displayName={member.display_name ?? "?"}
              size={32}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body font-medium">
                {member.display_name ?? t("memberUnnamed")}
                {member.user_id === me && (
                  <span className="ml-1.5 text-meta font-normal text-muted">{t("memberYou")}</span>
                )}
              </span>
            </span>
            <span className="shrink-0 text-meta text-muted">{t(ROLE_KEY[member.role])}</span>
          </li>
        ))}
      </ul>

      {/* Invitation controls for family owner */}
      {canManageMembers && (
        <div className="space-y-4 rounded-card border border-ink/[0.08] bg-raised/40 p-4">
          <div className="flex items-center gap-2">
            <UserPlus className="size-4 text-ink/70" />
            <h4 className="text-body font-semibold text-ink">{t("inviteMember")}</h4>
          </div>

          <div className="space-y-3">
            <div>
              <label htmlFor="invite-role-select" className="mb-1 block text-meta text-muted">
                {t("inviteRoleLabel")}
              </label>
              <select
                id="invite-role-select"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as "editor" | "viewer")}
                className="w-full rounded-control border border-ink/[0.12] bg-raised px-3 py-2 text-body text-ink outline-none focus-ring"
              >
                <option value="viewer">{t("inviteRoleViewer")}</option>
                <option value="editor">{t("inviteRoleEditor")}</option>
              </select>
            </div>

            <Button
              type="button"
              variant="primary"
              size="md"
              disabled={creating}
              onClick={handleCreateInvite}
              className="w-full"
            >
              {creating ? t("inviteCreating") : t("inviteCreateButton")}
            </Button>

            {error && <p className="text-meta text-danger">{error}</p>}

            {createdUrl && (
              <div className="mt-3 space-y-2 rounded-control bg-raised p-3 border border-ink/[0.06]">
                <p className="text-meta font-medium text-ink">{t("inviteLinkReady")}</p>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    readOnly
                    value={createdUrl}
                    className="min-w-0 flex-1 rounded-control bg-sand/30 px-2.5 py-1.5 text-meta text-ink select-all outline-none"
                  />
                  <Button
                    type="button"
                    variant="soft"
                    size="md"
                    onClick={handleCopy}
                    className="shrink-0 gap-1.5 px-3 py-1.5 text-meta h-9"
                  >
                    {copied ? (
                      <>
                        <Check className="size-3.5 text-ink" />
                        <span>{t("inviteCopied")}</span>
                      </>
                    ) : (
                      <>
                        <Copy className="size-3.5 text-ink" />
                        <span>{t("inviteCopyLink")}</span>
                      </>
                    )}
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Pending invitations list */}
          <div className="pt-3 border-t border-ink/[0.06]">
            <h5 className="mb-2 text-meta font-semibold text-muted uppercase tracking-wider">
              {t("invitePendingTitle")}
            </h5>

            {loadingInvitations && invitations.length === 0 ? (
              <p className="text-meta text-muted">{t("membersLoading")}</p>
            ) : invitations.length === 0 ? (
              <p className="text-meta text-muted">{t("inviteNoPending")}</p>
            ) : (
              <ul className="space-y-2">
                {invitations.map((inv) => {
                  const expiry = new Date(inv.expires_at).toLocaleDateString(locale, {
                    day: "numeric",
                    month: "short",
                  });
                  const roleLabelKey =
                    inv.role === "editor" ? "roleEditor" : "roleViewer";
                  return (
                    <li
                      key={inv.invitation_id}
                      className="flex items-center justify-between gap-3 rounded-control bg-raised/60 px-3 py-2 text-meta"
                    >
                      <div className="min-w-0 flex-1">
                        <span className="font-medium text-ink">{t(roleLabelKey)}</span>
                        <span className="ml-2 text-muted">
                          {t("inviteExpiresAt", { date: expiry })}
                        </span>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="md"
                        disabled={revokingId === inv.invitation_id}
                        onClick={() => handleRevoke(inv.invitation_id)}
                        className="h-8 px-2.5 text-danger hover:text-danger/80 gap-1 text-meta"
                      >
                        <X className="size-3.5" />
                        <span>
                          {revokingId === inv.invitation_id
                            ? t("inviteRevoking")
                            : t("inviteRevoke")}
                        </span>
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
