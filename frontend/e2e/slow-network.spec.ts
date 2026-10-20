import type { Page, Request } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { canvasBounds, devToken, drawBox, makePng, seedBatch, signIn, uniqueSeed } from "./helpers";

/**
 * A bad connection: high latency, low bandwidth, dropped sockets and short outages. Network
 * emulation goes through CDP, so these run on Chromium only.
 */
test.skip(({ browserName }) => browserName !== "chromium", "network throttling uses CDP");

type Conditions = { latency?: number; kbps?: number; offline?: boolean };

/**
 * One CDP session owns all network emulation for the page. (Mixing it with
 * `context.setOffline` does not work: each emulateNetworkConditions call overrides the other.)
 */
const useNetwork = async (page: Page) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  return async ({ latency = 0, kbps = -1, offline = false }: Conditions) => {
    const bytesPerSecond = kbps < 0 ? -1 : (kbps * 1024) / 8;
    await cdp.send("Network.emulateNetworkConditions", {
      offline,
      latency,
      downloadThroughput: bytesPerSecond,
      uploadThroughput: bytesPerSecond,
    });
  };
};

const newBatchPage = async (page: Page, name: string): Promise<string> => {
  await page.getByLabel("New batch name").fill(name);
  await page.getByRole("button", { name: "Create batch" }).click();
  await expect(page.getByRole("heading", { name: "Upload images" })).toBeVisible();
  const batchId = /batches\/([^/]+)\/upload/.exec(page.url())?.[1];
  expect(batchId).toBeTruthy();
  return batchId as string;
};

test("uploads retry after dropped connections on a slow link", async ({ page }) => {
  const stamp = Date.now();
  await signIn(page, `slow-up-${stamp}@example.com`, ["labeler"]);
  await newBatchPage(page, `Slow upload ${stamp}`);

  // Every file's first PUT to storage is cut mid-flight; the second attempt gets through.
  const puts = new Map<string, number>();
  await page.route(
    (url) => url.port === "4566",
    async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      const key = new URL(route.request().url()).pathname;
      const attempt = (puts.get(key) ?? 0) + 1;
      puts.set(key, attempt);
      return attempt === 1 ? route.abort("connectionreset") : route.continue();
    },
  );
  const network = await useNetwork(page);
  await network({ latency: 400, kbps: 1500 });

  const base = uniqueSeed();
  await page.getByTestId("file-input").setInputFiles([
    { name: "a.png", mimeType: "image/png", buffer: makePng(base, 400, 300) },
    { name: "b.png", mimeType: "image/png", buffer: makePng(base + 1, 400, 300) },
  ]);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByTestId("upload-summary")).toContainText("2 ready", { timeout: 60_000 });
  expect([...puts.values()]).toEqual([2, 2]); // one failure and one success each
});

test("a lease survives an outage, and the next heartbeat after it succeeds", async ({ page, request }) => {
  const stamp = Date.now();
  const seeder = await devToken(request, `slow-seed-${stamp}@example.com`, ["labeler"]);
  const batchId = await seedBatch(request, seeder, 1, [800, 600]);
  await signIn(page, `slow-hb-${stamp}@example.com`, ["labeler"]);

  const heartbeats = { failed: 0, ok: 0 };
  const isBeat = (r: Request) => r.url().includes("/heartbeat");
  page.on("requestfailed", (r) => isBeat(r) && (heartbeats.failed += 1));
  page.on("response", (r) => isBeat(r.request()) && r.status() === 200 && (heartbeats.ok += 1));

  // Fake timers so the 60 s heartbeat cadence can be stepped through instantly.
  await page.clock.install();
  await page.goto(`/batches/${batchId}/label`);
  await canvasBounds(page);
  const network = await useNetwork(page);
  await network({ latency: 1200, kbps: 400 });

  await network({ latency: 1200, kbps: 400, offline: true });
  await page.clock.runFor(61_000);
  await expect.poll(() => heartbeats.failed).toBeGreaterThanOrEqual(1);
  // A failed beat is not a lost lease: the user keeps their image and their work.
  await expect(page.getByRole("heading", { name: "This image is no longer yours" })).toHaveCount(0);
  await expect(page.getByTestId("canvas")).toBeVisible();

  await network({ latency: 1200, kbps: 400 });
  await page.clock.runFor(61_000);
  await expect.poll(() => heartbeats.ok, { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
  await expect(page.getByRole("heading", { name: "This image is no longer yours" })).toHaveCount(0);

  // Still the same lease: label it and submit over the slow link.
  await drawBox(page, [0.2, 0.2], [0.6, 0.6], "car");
  await page.getByRole("button", { name: /Submit/ }).click();
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible({ timeout: 30_000 });
});

test("a submit that fails offline keeps the boxes and succeeds when retried online", async ({ page, request }) => {
  const stamp = Date.now();
  const seeder = await devToken(request, `off-seed-${stamp}@example.com`, ["labeler"]);
  const batchId = await seedBatch(request, seeder, 1, [800, 600]);
  await signIn(page, `off-sub-${stamp}@example.com`, ["labeler"]);
  await page.goto(`/batches/${batchId}/label`);
  await canvasBounds(page);

  await drawBox(page, [0.2, 0.2], [0.6, 0.6], "car");
  const network = await useNetwork(page);
  await network({ offline: true });
  await page.getByRole("button", { name: /Submit/ }).click();
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("list", { name: "Boxes" }).getByRole("listitem")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "All caught up" })).toHaveCount(0);

  await network({});
  await page.getByRole("button", { name: /Submit/ }).click();
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible({ timeout: 30_000 });
});
