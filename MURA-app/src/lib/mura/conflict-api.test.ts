import { afterEach, describe, expect, it, vi } from "vitest";
import { decideConflict, fetchConflicts } from "@/lib/mura/conflict-api";

const FAMILY = `family_${"a".repeat(32)}`;
const CONFLICT = `conflict_${"b".repeat(32)}`;

afterEach(() => vi.unstubAllGlobals());

describe("family-scoped conflict decisions", () => {
  it("reads the canonical conflict list without a mutation", async () => {
    const fetchSpy = vi.fn(async () => Response.json([]));
    vi.stubGlobal("fetch", fetchSpy);

    expect(await fetchConflicts(FAMILY)).toEqual([]);
    const [url] = fetchSpy.mock.calls[0] as unknown as [string];
    expect(url).toBe(`/api/mura/v1/families/${FAMILY}/conflicts`);
  });

  it("posts only the selected claim and an explicit provenance note", async () => {
    const fetchSpy = vi.fn(async () => Response.json({ conflict: { status: "resolved" } }));
    vi.stubGlobal("fetch", fetchSpy);

    await decideConflict(FAMILY, CONFLICT, "resolve", `user_${"c".repeat(32)}`, "Narrator clarified", "claim_one");

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/mura/v1/families/${FAMILY}/conflicts/${CONFLICT}/resolve`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      reviewer_reference: `user_${"c".repeat(32)}`,
      note: "Narrator clarified",
      preferred_claim_id: "claim_one",
    });
  });

  it("does not invent a preferred claim on dismiss or reopen", async () => {
    const fetchSpy = vi.fn(async () => Response.json({ conflict: { status: "dismissed" } }));
    vi.stubGlobal("fetch", fetchSpy);

    await decideConflict(FAMILY, CONFLICT, "dismiss", "user_one", "Both are uncertain");
    await decideConflict(FAMILY, CONFLICT, "reopen", "user_one", "New evidence");

    for (const [, init] of fetchSpy.mock.calls as unknown as [string, RequestInit][]) {
      expect(JSON.parse(init.body as string)).not.toHaveProperty("preferred_claim_id");
    }
  });
});
