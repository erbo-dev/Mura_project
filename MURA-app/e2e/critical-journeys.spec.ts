import { expect, test } from "@playwright/test";
import { conflictId, familyId, mockApplication } from "./fixture";

test("bootstrap session and create the first family", async ({ page }) => {
  const state = await mockApplication(page, { signedIn: false, hasFamily: false });
  await page.goto("/sign-in");
  await page.locator("#dev-auth-email").fill("fixture@example.test");
  await page.locator("#dev-auth-email").press("Enter");
  await expect.poll(() => state.signedIn).toBe(true);
  await expect(page).toHaveURL(/\/home/, { timeout: 30_000 });
  await page.locator("#family-name").fill("New family fixture");
  await page.locator("#family-name").press("Enter");
  await expect.poll(() => state.families.length).toBe(1);
  expect(state.families[0].name).toBe("New family fixture");
});

test("Human Review resolves a claim with its evidence and source", async ({ page }) => {
  const state = await mockApplication(page);
  await page.goto("/review");
  await expect(page.getByText("Narrator statement 1")).toBeVisible();
  await expect(page.locator(`audio[src*="${familyId}"]`)).toHaveCount(2);
  await page.locator(`input[name="claim-${conflictId}"][value="claim_a"]`).check();
  await page.locator(`#note-${conflictId}`).fill("Family confirmed");
  await page.getByRole("button", { name: /выбрать|resolve|таңдау/i }).click();
  await expect.poll(() => state.decision).toBe("resolved");
  await expect(page.getByText("Family confirmed")).toBeVisible();
});

test("viewer reads evidence but cannot submit a decision", async ({ page }) => {
  const state = await mockApplication(page, { viewer: true });
  await page.goto("/review");
  await expect(page.getByText("Narrator statement 1")).toBeVisible();
  await expect(page.locator(`#note-${conflictId}`)).toHaveCount(0);
  expect(state.requests.some(({ method }) => method === "POST")).toBe(false);
});

test("account deletion blocks a sole owner and keeps the session", async ({ page }) => {
  const state = await mockApplication(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "English" }).click();
  await page.getByRole("button", { name: "Delete account" }).click();
  await page.locator("#delete-account-confirmation").fill("DELETE");
  await page.getByRole("button", { name: "Permanently delete" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "sole owner" })).toBeVisible();
  expect(state.signedIn).toBe(true);
  expect(state.requests.filter(({ method, path }) => method === "DELETE" && path === "/v1/me")).toHaveLength(1);
});

test("account deletion confirms Core only, clears local state, and signs out", async ({ page }) => {
  const state = await mockApplication(page, { deletion: "allowed" });
  await page.goto("/settings");
  await page.getByRole("button", { name: "English" }).click();
  await page.evaluate(() => localStorage.setItem("mura.selectedFamilyId", "private-family"));
  await page.getByRole("button", { name: "Delete account" }).click();
  await page.locator("#delete-account-confirmation").fill("DELETE");
  await page.getByRole("button", { name: "Permanently delete" }).click();
  await expect.poll(() => state.signedIn).toBe(false);
  expect(state.requests.filter(({ method, path }) => method === "DELETE" && path === "/v1/me")).toHaveLength(1);
  await page.waitForURL((url) => url.pathname === "/" || url.pathname === "/sign-in");
  await page.waitForLoadState("domcontentloaded");
  expect(await page.evaluate(() => localStorage.getItem("mura.selectedFamilyId"))).toBeNull();
});
