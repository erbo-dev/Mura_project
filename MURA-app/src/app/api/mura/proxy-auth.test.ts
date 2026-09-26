/**
 * The proxy's security behaviour, not just its route table.
 *
 * These assertions exist because the previous proxy authenticated every browser
 * request with the Core service credential. Core has since stopped accepting
 * that credential for family data, but the frontend must not be one edit away
 * from reintroducing the hole -- so the absence of the service token, and the
 * refusal to forward a caller-supplied bearer, are pinned here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerAuthSession } from "@/lib/auth/server-session";

const readServerAuthSession = vi.fn<(request: Request) => Promise<ServerAuthSession>>();

vi.mock("@/lib/auth/server-session", () => ({
  readServerAuthSession: (request: Request) => readServerAuthSession(request),
}));

const { handleCoreProxy } = await import("./[...path]/proxy");

const CORE_URL = "https://core.internal";
const USER_TOKEN = "user-access-token-value";
const SERVICE_TOKEN = "core-service-credential-value";
const FAMILY = `family_${"a".repeat(32)}`;
const RECORDING = `rec_${"a".repeat(32)}`;

function signedIn(): void {
  readServerAuthSession.mockResolvedValue({
    status: "authenticated",
    accessToken: USER_TOKEN,
  });
}

function upstream(body: unknown = { ok: true }, status = 200) {
  return vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

function get(route: string, headers: Record<string, string> = {}): Request {
  return new Request(`https://app.example/api/mura/${route}`, { headers });
}

beforeEach(() => {
  vi.stubEnv("MURA_API_URL", CORE_URL);
  // Deliberately present in the environment: the assertions below prove the
  // proxy does not reach for it even when it is sitting right there.
  vi.stubEnv("MURA_CORE_API_KEY", SERVICE_TOKEN);
  readServerAuthSession.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("session boundary", () => {
  it("forwards the user's bearer to Core", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const response = await handleCoreProxy(get("v1/me"), ["v1", "me"]);

    expect(response.status).toBe(200);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE_URL}/v1/me`);
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${USER_TOKEN}`);
  });

  it("never injects the Core service credential", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    await handleCoreProxy(get("v1/families"), ["v1", "families"]);

    const init = (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1];
    const authorization = new Headers(init.headers).get("authorization") ?? "";
    expect(authorization).not.toContain(SERVICE_TOKEN);
  });

  it("refuses a request with no session instead of falling back", async () => {
    readServerAuthSession.mockResolvedValue({ status: "unauthenticated" });
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const response = await handleCoreProxy(get("v1/families"), ["v1", "families"]);

    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("authentication_required");
    // The point of the test: no upstream call was made at all.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("reports an unconfigured provider distinctly from a signed-out user", async () => {
    readServerAuthSession.mockResolvedValue({ status: "provider_unconfigured" });
    vi.stubGlobal("fetch", upstream());

    const response = await handleCoreProxy(get("v1/me"), ["v1", "me"]);

    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("auth_provider_unconfigured");
  });

  it("ignores a caller-supplied Authorization header", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    await handleCoreProxy(
      get("v1/me", { authorization: `Bearer ${SERVICE_TOKEN}` }),
      ["v1", "me"],
    );

    const init = (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${USER_TOKEN}`);
  });

  it("does not let an unauthenticated caller borrow its own bearer", async () => {
    readServerAuthSession.mockResolvedValue({ status: "unauthenticated" });
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const response = await handleCoreProxy(
      get("v1/me", { authorization: "Bearer smuggled-token" }),
      ["v1", "me"],
    );

    expect(response.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("blocked surfaces", () => {
  const blocked: ReadonlyArray<[string, string[]]> = [
    ["v1/process-transcript", ["v1", "process-transcript"]],
    ["v1/jobs/job_x/trace", ["v1", "jobs", `job_${"b".repeat(32)}`, "trace"]],
    ["v1/families/x/replays", ["v1", "families", FAMILY, "replays"]],
    ["v1/operations/release", ["v1", "operations", "release"]],
    ["v1/operations/retention/apply", ["v1", "operations", "retention", "apply"]],
    ["v1/workers/register", ["v1", "workers", "register"]],
    ["v1/workers/current", ["v1", "workers", "current"]],
  ];

  it.each(blocked)("rejects %s before any session or upstream work", async (_label, path) => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const response = await handleCoreProxy(get(path.join("/")), path as string[]);

    expect(response.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects traversal", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const response = await handleCoreProxy(get("v1/..%2Fv1/operations/release"), [
      "v1",
      "..",
      "v1",
      "operations",
      "release",
    ]);

    expect(response.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("allows account deletion only with the authenticated user's token", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);

    const request = new Request(`https://app.example/api/mura/v1/me`, { method: "DELETE" });
    const response = await handleCoreProxy(request, ["v1", "me"]);

    expect(response.status).toBe(200);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE_URL}/v1/me`);
    expect(init.method).toBe("DELETE");
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${USER_TOKEN}`);
  });
});

describe("binary transport", () => {
  it("forwards only the audio Range and selected 206 response headers", async () => {
    signedIn();
    const fetchSpy = vi.fn(async () => new Response(new Uint8Array([3, 4]), {
      status: 206,
      headers: {
        "content-type": "audio/webm",
        "content-length": "2",
        "content-range": "bytes 2-3/8",
        "accept-ranges": "bytes",
        "set-cookie": "private=leak",
        "authorization": "Bearer upstream-secret",
      },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const route = `v1/families/${FAMILY}/recordings/${RECORDING}/audio`;

    const response = await handleCoreProxy(get(route, {
      range: "bytes=2-3", authorization: "Bearer browser-forgery", "if-range": "fake",
    }), route.split("/"));

    expect(response.status).toBe(206);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([3, 4]));
    const init = (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).get("range")).toBe("bytes=2-3");
    expect(new Headers(init.headers).get("if-range")).toBeNull();
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${USER_TOKEN}`);
    expect(response.headers.get("content-range")).toBe("bytes 2-3/8");
    expect(response.headers.get("accept-ranges")).toBe("bytes");
    expect(response.headers.get("content-length")).toBe("2");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("authorization")).toBeNull();
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(init.signal?.aborted).toBe(false);
  });

  it("preserves Core's safe PDF/EPUB filename and no upstream cookie", async () => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([37, 80]), {
      headers: {
        "content-type": "application/pdf",
        "content-disposition": "attachment; filename*=UTF-8''family-book.pdf",
        "set-cookie": "private=leak",
      },
    })));
    const route = `v1/families/${FAMILY}/books/book_${"a".repeat(32)}/download`;
    const response = await handleCoreProxy(get(route), route.split("/"));

    expect(response.headers.get("content-disposition")).toContain("family-book.pdf");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([37, 80]));
  });

  it("never forwards Range or upstream binary headers on ordinary JSON", async () => {
    signedIn();
    const fetchSpy = vi.fn(async () => new Response("{}", {
      headers: { "content-disposition": "attachment; filename=unsafe", "content-length": "2" },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const response = await handleCoreProxy(get("v1/me", { range: "bytes=0-1" }), ["v1", "me"]);

    expect(new Headers((fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers).get("range")).toBeNull();
    expect(response.headers.get("content-disposition")).toBeNull();
    expect(response.headers.get("content-length")).toBeNull();
  });
});

describe("request bodies", () => {
  it("forwards a JSON confirmation body on DELETE", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);
    const payload = JSON.stringify({ confirm_family_id: FAMILY });
    const request = new Request(
      `https://app.example/api/mura/v1/families/${FAMILY}`,
      {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        body: payload,
      },
    );

    const response = await handleCoreProxy(request, ["v1", "families", FAMILY]);

    expect(response.status).toBe(200);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE_URL}/v1/families/${FAMILY}`);
    expect(init.method).toBe("DELETE");
    expect(init.body).toBe(payload);
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
  });

  it("keeps a bodyless DELETE bodyless", async () => {
    signedIn();
    const fetchSpy = upstream();
    vi.stubGlobal("fetch", fetchSpy);
    const request = new Request(
      `https://app.example/api/mura/v1/families/${FAMILY}/recordings/${RECORDING}`,
      { method: "DELETE" },
    );

    await handleCoreProxy(request, [
      "v1",
      "families",
      FAMILY,
      "recordings",
      RECORDING,
    ]);

    const init = (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.body).toBeUndefined();
  });
});

describe("responses never leak the token", () => {
  it("keeps the bearer out of an upstream failure", async () => {
    signedIn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error(`connect ECONNREFUSED with Bearer ${USER_TOKEN}`);
      }),
    );

    const response = await handleCoreProxy(get("v1/families"), ["v1", "families"]);
    const text = await response.text();

    expect(response.status).toBe(503);
    expect(text).not.toContain(USER_TOKEN);
    expect(text).not.toContain(SERVICE_TOKEN);
  });

  it("keeps the bearer out of a Core error passed through", async () => {
    signedIn();
    vi.stubGlobal(
      "fetch",
      upstream(
        {
          error: {
            code: "family_not_found",
            message: "The requested resource was not found.",
            retryable: false,
            request_id: "req_1",
          },
        },
        404,
      ),
    );

    const response = await handleCoreProxy(
      get(`v1/families/${FAMILY}/recordings/${RECORDING}`),
      ["v1", "families", FAMILY, "recordings", RECORDING],
    );
    const text = await response.text();

    expect(response.status).toBe(404);
    expect(text).not.toContain(USER_TOKEN);
    expect(JSON.parse(text).error.code).toBe("family_not_found");
  });

  it("marks family responses uncacheable so no cache can cross sessions", async () => {
    signedIn();
    vi.stubGlobal("fetch", upstream());

    const response = await handleCoreProxy(get("v1/families"), ["v1", "families"]);

    expect(response.headers.get("cache-control")).toContain("no-store");
  });
});

describe("configuration", () => {
  it("needs only the Core URL, never a service credential", async () => {
    signedIn();
    vi.stubEnv("MURA_CORE_API_KEY", "");
    vi.stubGlobal("fetch", upstream());

    const response = await handleCoreProxy(get("v1/me"), ["v1", "me"]);

    expect(response.status).toBe(200);
  });

  it("reports an outage when Core has no address", async () => {
    signedIn();
    vi.stubEnv("MURA_API_URL", "");
    vi.stubGlobal("fetch", upstream());

    const response = await handleCoreProxy(get("v1/me"), ["v1", "me"]);

    expect(response.status).toBe(503);
    expect((await response.json()).error.code).toBe("service_unavailable");
  });
});

describe("error envelopes are never cacheable", () => {
  it("refuses caching on the unauthenticated envelope", async () => {
    // A cached 401 survives the sign-in that would fix it, leaving a user with
    // a live session still being told to log in.
    readServerAuthSession.mockResolvedValue({ status: "unauthenticated" });
    const response = await handleCoreProxy(get("v1/me"), ["v1", "me"]);

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store, private");
  });

  it("refuses caching on the unknown-route envelope", async () => {
    const response = await handleCoreProxy(
      get("v1/operations/release"),
      ["v1", "operations", "release"],
    );

    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store, private");
  });
});
