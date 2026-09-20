import { afterEach, describe, expect, it, vi } from "vitest";
import { deleteBook } from "./book-api";

const FAMILY = `family_${"a".repeat(32)}`;
const BOOK = `book_${"b".repeat(32)}`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("deleteBook API", () => {
  it("issues DELETE request to canonical book route", async () => {
    const spy = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", spy);

    await deleteBook(FAMILY, BOOK);

    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/mura/v1/families/${FAMILY}/books/${BOOK}`);
    expect(init.method).toBe("DELETE");
  });
});

