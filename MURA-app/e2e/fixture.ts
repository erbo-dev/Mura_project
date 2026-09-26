import type { Page } from "@playwright/test";

export const familyId = `family_${"a".repeat(32)}`;
export const userId = `user_${"b".repeat(32)}`;
export const recordingId = `rec_${"c".repeat(32)}`;
export const bookId = `book_${"d".repeat(32)}`;
export const conflictId = "conflict_review_test";

export type FixtureState = {
  signedIn: boolean;
  families: Array<{ family_id: string; name: string; role: string; capabilities: string[] }>;
  decision: "open" | "resolved" | "dismissed";
  deletion: "blocked" | "allowed";
  requests: Array<{ method: string; path: string }>;
};

const family = { family_id: familyId, name: "Archive fixture", role: "owner", capabilities: ["resolve_conflicts", "create_recording", "create_book"] };
const reply = (route: Parameters<Parameters<Page["route"]>[1]>[0], data: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });

export async function mockApplication(page: Page, options: { signedIn?: boolean; hasFamily?: boolean; viewer?: boolean; deletion?: "blocked" | "allowed" } = {}): Promise<FixtureState> {
  const state: FixtureState = {
    signedIn: options.signedIn ?? true,
    families: options.hasFamily === false ? [] : [{ ...family, role: options.viewer ? "viewer" : "owner", capabilities: options.viewer ? [] : family.capabilities }],
    decision: "open",
    deletion: options.deletion ?? "blocked",
    requests: [],
  };
  await page.route("**/api/dev-auth/session", async (route) => {
    const method = route.request().method();
    if (method === "POST") state.signedIn = true;
    if (method === "DELETE") state.signedIn = false;
    await reply(route, { signedIn: state.signedIn, sessionKey: state.signedIn ? "test-session" : null });
  });
  await page.route("**/api/mura/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/mura", "");
    const method = route.request().method();
    state.requests.push({ method, path });
    if (!state.signedIn) return reply(route, { error: { code: "authentication_required", message: "Sign in", retryable: false, request_id: "test" } }, 401);
    if (path === "/v1/me" && method === "GET") return reply(route, { user_id: userId, email: "fixture@example.test", display_name: "Fixture Person" });
    if (path === "/v1/me" && method === "DELETE") {
      if (state.deletion === "blocked") return reply(route, { error: { code: "account_deletion_requires_owner_transfer", message: "Owner transfer required", retryable: false, request_id: "test" } }, 409);
      return reply(route, { mura_data_deleted: true, identity_provider_account_deleted: false, requires_provider_sign_out: true });
    }
    if (path === "/v1/capabilities") return reply(route, {
      schema_version: "1", status: "ready", recording: { enabled: true, mode: "audio" },
      asr: { registration: "registered", registered_at: null, registration_age_seconds: null, live_health_verified: false },
      analysis: { configuration: "configured", live_health_verified: false }, validation: { release_version: "fixture" },
    });
    if (path === "/v1/families" && method === "GET") return reply(route, state.families);
    if (path === "/v1/families" && method === "POST") {
      state.families.push({ ...family, name: (route.request().postDataJSON() as { name: string }).name });
      return reply(route, state.families.at(-1), 201);
    }
    if (path === `/v1/families/${familyId}`) return reply(route, state.families[0]);
    if (path === `/v1/families/${familyId}/members`) return reply(route, [{ user_id: userId, role: "owner", display_name: "Fixture Person" }]);
    if (path === `/v1/families/${familyId}/review-items`) return reply(route, []);
    if (path === `/v1/families/${familyId}/conflicts` && method === "GET") return reply(route, [{
      conflict_id: conflictId, family_id: familyId, status: state.decision, rationale: "Two accounts disagree",
      preferred_claim_id: state.decision === "resolved" ? "claim_a" : null, resolution_note: state.decision === "resolved" ? "Family confirmed" : null, decisions: [],
      claims: ["claim_a", "claim_b"].map((claim_id, index) => ({
        claim_id, recording_id: recordingId, source_object_id: "story_fixture", object_type: "relationship",
        predicate: "parent_child", status: "disputed", verification_status: "unverified", evidence_ids: [`evidence_${index}`],
        evidence_quotes: [`Narrator statement ${index + 1}`], payload: { value: `Alternative ${index + 1}` },
      })),
    }]);
    if (path.startsWith(`/v1/families/${familyId}/conflicts/${conflictId}/`) && method === "POST") {
      if (options.viewer) return reply(route, { error: { code: "forbidden", message: "Denied", retryable: false, request_id: "test" } }, 403);
      state.decision = path.endsWith("/resolve") ? "resolved" : path.endsWith("/dismiss") ? "dismissed" : "open";
      return reply(route, { conflict: { status: state.decision } });
    }
    return reply(route, { error: { code: "not_found", message: "Unknown fixture route", retryable: false, request_id: "test" } }, 404);
  });
  return state;
}
