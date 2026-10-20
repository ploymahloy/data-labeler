import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { API_URL, devToken, seedBatch, signIn } from "./helpers";

/**
 * The list is paged and ordered by the identity provider, so on a busy pool a given user may be
 * several "Load more" clicks away.
 */
const findUser = async (page: Page, email: string) => {
  const row = page.getByTestId(`user-${email}`);
  const more = page.getByRole("button", { name: "Load more" });
  await expect(page.getByRole("table")).toBeVisible();
  for (let i = 0; i < 40 && (await row.count()) === 0 && (await more.count()) > 0; i += 1) {
    await Promise.all([page.waitForResponse((r) => r.url().includes("/admin/users?") && r.url().includes("token=")), more.click()]);
  }
  await expect(row).toBeVisible();
  return row;
};

test("archive blocks work, unarchive restores it, delete needs the batch name", async ({ page, request }) => {
  const stamp = Date.now();
  const labeler = await devToken(request, `arch-lab-${stamp}@example.com`, ["labeler"]);
  const admin = await devToken(request, `arch-adm-${stamp}@example.com`, ["admin"]);
  const batchId = await seedBatch(request, labeler, 2);
  const name = ((await (await request.get(`${API_URL}/batches/${batchId}`, { headers: { Authorization: `Bearer ${admin}` } })).json()) as { name: string }).name;
  const claim = (token: string) =>
    request.post(`${API_URL}/batches/${batchId}/images/next`, { headers: { Authorization: `Bearer ${token}` } });

  await signIn(page, `arch-adm-${stamp}@example.com`, ["admin"]);
  await page.goto(`/batches/${batchId}`);

  // Archive: a labeler can no longer claim work.
  await page.getByRole("button", { name: "Archive batch" }).click();
  await page.getByRole("button", { name: "Confirm archive" }).click();
  await expect(page.getByText(/This batch is archived \(/)).toBeVisible();
  const blocked = await claim(labeler);
  expect(blocked.status()).toBe(409);
  expect(((await blocked.json()) as { detail: { code: string } }).detail.code).toBe("batch_archived");

  // Archived batches are hidden from the list unless asked for.
  await page.goto("/");
  await expect(page.getByRole("link", { name })).toHaveCount(0);
  await page.getByLabel("Show archived batches").check();
  await expect(page.getByRole("link", { name })).toBeVisible();

  // Unarchive: work resumes.
  await page.goto(`/batches/${batchId}`);
  await page.getByRole("button", { name: "Unarchive" }).click();
  await expect(page.getByRole("button", { name: "Archive batch" })).toBeVisible();
  expect((await claim(labeler)).status()).toBe(200);

  // Delete: only possible when archived, and only after typing the exact name.
  await page.getByRole("button", { name: "Archive batch" }).click();
  await page.getByRole("button", { name: "Confirm archive" }).click();
  await page.getByRole("button", { name: "Delete permanently…" }).click();
  const forever = page.getByRole("button", { name: "Delete forever" });
  await expect(forever).toBeDisabled();
  await page.getByLabel("Batch name").fill(`${name} nope`);
  await expect(forever).toBeDisabled();
  await page.getByLabel("Batch name").fill(name);
  await expect(forever).toBeEnabled();
  await forever.click();
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  const gone = await request.get(`${API_URL}/batches/${batchId}`, { headers: { Authorization: `Bearer ${admin}` } });
  expect(gone.status()).toBe(404);
});

test("an admin invites a user, changes their roles, and disables them", async ({ page }) => {
  const stamp = Date.now();
  await signIn(page, `users-adm-${stamp}@example.com`, ["admin"]);
  await page.getByRole("link", { name: "Users" }).click();
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();

  const email = `invitee-${stamp}@example.com`;
  await page.getByRole("form", { name: "Invite user" }).getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send invite" }).click();
  const row = await findUser(page, email);

  // A click, not check(): the box only flips once the server has accepted the change.
  await row.getByRole("checkbox", { name: "reviewer" }).click();
  await expect(row.getByRole("checkbox", { name: "reviewer" })).toBeChecked();

  await row.getByRole("button", { name: "Disable" }).click();
  await expect(row).toContainText("disabled");
  await row.getByRole("button", { name: "Enable" }).click();
  await expect(row).not.toContainText("disabled");

  // You cannot lock yourself out.
  const self = await findUser(page, `users-adm-${stamp}@example.com`);
  await expect(self.getByRole("button", { name: "Disable" })).toBeDisabled();
});
