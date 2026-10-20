import type { Browser, Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { canvasBounds, drawBox, makePng, signIn, uniqueSeed } from "./helpers";

const newUserPage = async (browser: Browser, email: string, roles: string[]): Promise<Page> => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, email, roles);
  return page;
};

test("upload -> label -> reject -> rework -> accept, with an independent reviewer", async ({ browser }) => {
  const stamp = Date.now();
  const ana = await newUserPage(browser, `ana-${stamp}@example.com`, ["labeler", "reviewer"]);

  // --- upload through the UI (direct-to-S3) ---------------------------------------------------
  await ana.getByLabel("New batch name").fill(`UI batch ${stamp}`);
  await ana.getByRole("button", { name: "Create batch" }).click();
  await expect(ana.getByRole("heading", { name: "Upload images" })).toBeVisible();
  const batchId = /batches\/([^/]+)\/upload/.exec(ana.url())?.[1];
  expect(batchId).toBeTruthy();

  const base = uniqueSeed();
  const first = makePng(base);
  const second = makePng(base + 1);
  await ana.getByTestId("file-input").setInputFiles([
    { name: "one.png", mimeType: "image/png", buffer: first },
    { name: "two.png", mimeType: "image/png", buffer: second },
    { name: "one-copy.png", mimeType: "image/png", buffer: first }, // identical bytes
    { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") },
  ]);
  await expect(ana.getByRole("alert")).toContainText("notes.txt"); // rejected client-side
  await ana.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(ana.getByTestId("upload-summary")).toContainText("2 ready · 1 duplicates", { timeout: 30_000 });

  // --- label both images ------------------------------------------------------------------------
  await ana.goto(`/batches/${batchId}/label`);
  await drawBox(ana, [0.1, 0.15], [0.5, 0.6], "car");
  await drawBox(ana, [0.55, 0.2], [0.9, 0.8], "Traffic Light");
  await expect(ana.getByRole("list", { name: "Boxes" }).getByRole("listitem")).toHaveCount(2);
  await ana.getByRole("button", { name: /Submit/ }).click();

  await expect(ana.getByText("0 submitted this session")).toHaveCount(0);
  await drawBox(ana, [0.2, 0.2], [0.7, 0.7], "dog");
  await ana.getByRole("button", { name: /Submit/ }).click();
  await expect(ana.getByRole("heading", { name: "All caught up" })).toBeVisible();

  // The annotator also holds the reviewer role but is never offered their own work.
  await ana.goto(`/batches/${batchId}/review`);
  await expect(ana.getByRole("heading", { name: "Nothing to review" })).toBeVisible();

  // --- an independent reviewer rejects one image and accepts the other -----------------------------
  const rev = await newUserPage(browser, `rev-${stamp}@example.com`, ["reviewer"]);
  await rev.goto(`/batches/${batchId}/review`);
  await canvasBounds(rev);
  const imageName = rev.locator("aside h3");
  const firstFile = await imageName.innerText();
  await rev.getByRole("button", { name: "Reject (R)" }).click();
  const dialog = rev.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Reject", exact: true })).toBeDisabled(); // reason required
  await dialog.getByLabel("Reason for rejection").fill("Boxes are too loose, please tighten them");
  await dialog.getByRole("button", { name: "Reject", exact: true }).click();

  await expect(imageName).not.toHaveText(firstFile); // moved on to the other image
  await canvasBounds(rev);
  await rev.getByRole("button", { name: "Accept (A)" }).click();
  await expect(rev.getByRole("heading", { name: "Nothing to review" })).toBeVisible();

  // --- rework: reserved for the original annotator, who sees the feedback -------------------------------
  await ana.goto(`/batches/${batchId}/label`);
  await expect(ana.getByTestId("rework-banner")).toContainText("Boxes are too loose");
  await canvasBounds(ana);
  await expect(ana.getByRole("list", { name: "Boxes" }).getByRole("listitem")).not.toHaveCount(0);
  await ana.getByRole("button", { name: /Submit/ }).click();
  await expect(ana.getByRole("heading", { name: "All caught up" })).toBeVisible();

  // --- a different reviewer accepts the resubmission --------------------------------------------------------
  const rev2 = await newUserPage(browser, `rev2-${stamp}@example.com`, ["reviewer"]);
  await rev2.goto(`/batches/${batchId}/review`);
  await expect(rev2.getByTestId("previous-feedback")).toContainText("Boxes are too loose");
  await rev2.getByRole("button", { name: "Accept (A)" }).click();
  await expect(rev2.getByRole("heading", { name: "Nothing to review" })).toBeVisible();

  // --- admin view: progress, stats, export ------------------------------------------------------------------
  const admin = await newUserPage(browser, `admin-${stamp}@example.com`, ["admin"]);
  await admin.goto(`/batches/${batchId}`);
  await expect(admin.getByTestId("count-accepted")).toHaveText("2");
  await expect(admin.getByTestId("review-stats")).toContainText(`rev-${stamp}@example.com`);

  const downloadPromise = admin.waitForEvent("download");
  await admin.getByRole("button", { name: "Download COCO JSON" }).click();
  const download = await downloadPromise;
  const path = await download.path();
  const coco = JSON.parse((await import("node:fs")).readFileSync(path, "utf8")) as {
    images: unknown[];
    annotations: unknown[];
    categories: { name: string }[];
  };
  expect(coco.images).toHaveLength(2);
  expect(coco.categories.map((c) => c.name).sort()).toEqual(["Traffic Light", "car", "dog"].sort());
  expect(coco.annotations).toHaveLength(3);
});

test("a lease that was lost is surfaced instead of silently losing work", async ({ browser, request }) => {
  const stamp = Date.now();
  const page = await newUserPage(browser, `lost-${stamp}@example.com`, ["labeler"]);
  await page.getByLabel("New batch name").fill(`Lease batch ${stamp}`);
  await page.getByRole("button", { name: "Create batch" }).click();
  await expect(page.getByRole("heading", { name: "Upload images" })).toBeVisible();
  const batchId = /batches\/([^/]+)\/upload/.exec(page.url())?.[1] as string;
  expect(batchId).toBeTruthy();
  await page.getByTestId("file-input").setInputFiles([
    { name: "a.png", mimeType: "image/png", buffer: makePng(uniqueSeed()) },
  ]);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByTestId("upload-summary")).toContainText("1 ready", { timeout: 30_000 });

  await page.goto(`/batches/${batchId}/label`);
  await canvasBounds(page);
  // The user releases the lease out-of-band (e.g. another tab): the next write must say so.
  const token = await page.evaluate(() => (JSON.parse(localStorage.getItem("labeler.session") ?? "{}") as { idToken: string }).idToken);
  const claimed = await request.post(`http://localhost:8000/batches/${batchId}/images/next`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const { id } = (await claimed.json()) as { id: string };
  await request.post(`http://localhost:8000/images/${id}/release`, { headers: { Authorization: `Bearer ${token}` } });

  await drawBox(page, [0.2, 0.2], [0.6, 0.6], "car");
  await expect(page.getByRole("heading", { name: "This image is no longer yours" })).toBeVisible({
    timeout: 15_000,
  });
});
