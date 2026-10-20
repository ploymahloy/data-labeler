import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { at, canvasBounds, devToken, seedBatch, signIn, touchDrag } from "./helpers";

/**
 * Phone-sized, touch-first coverage of the labeling screen. Runs in the `mobile-chrome` (Pixel 7,
 * real CDP touch input) and `mobile-safari` (iPhone 14 on WebKit, synthetic touch pointer events)
 * projects.
 */
const openLabelPage = async (page: Page, request: Parameters<typeof devToken>[0]) => {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const seeder = await devToken(request, `seed-${stamp}@example.com`, ["labeler"]);
  // A photo-sized image, as in real use: wider than a phone screen, so it must shrink to fit.
  const batchId = await seedBatch(request, seeder, 1, [1200, 800]);
  await signIn(page, `phone-${stamp}@example.com`, ["labeler"]);
  await page.goto(`/batches/${batchId}/label`);
  return canvasBounds(page);
};

test("the labeling screen fits a phone without sideways scrolling", async ({ page, request }) => {
  const canvas = await openLabelPage(page, request);
  const viewport = page.viewportSize();
  if (!viewport) throw new Error("no viewport");

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  expect(canvas.width).toBeGreaterThan(viewport.width * 0.8); // not squeezed beside a sidebar

  // The panel with Submit/Skip stacks under the image, and its buttons are thumb-sized.
  const side = await page.locator("aside").boundingBox();
  expect(side?.y ?? 0).toBeGreaterThanOrEqual(canvas.y + canvas.height - 1);
  const submit = await page.getByRole("button", { name: /Submit/ }).boundingBox();
  expect(submit?.height ?? 0).toBeGreaterThanOrEqual(44);
  await page.screenshot({ path: `test-results/phone-label-${test.info().project.name}.png`, fullPage: true });
});

test("draw, move, resize, rename and submit with a finger", async ({ page, request }) => {
  await openLabelPage(page, request);
  const boxes = page.getByRole("list", { name: "Boxes" }).getByRole("listitem");

  // Draw.
  const canvas = await canvasBounds(page);
  await touchDrag(page, at(canvas, 0.15, 0.2), at(canvas, 0.55, 0.65));
  const input = page.getByLabel("Entity name");
  await expect(input).toBeVisible();
  await input.fill("car");
  await input.press("Enter");
  const box = page.locator('[data-testid^="box-"]').first();
  await expect(box).toHaveAttribute("aria-label", "car box");
  await expect(box).toBeVisible();
  await expect(boxes).toHaveCount(1);

  // Move: grab the middle of the box and drag it right and down.
  const before = (await box.boundingBox())!;
  await touchDrag(page, at(before, 0.5, 0.5), { x: before.x + before.width * 0.5 + 40, y: before.y + before.height * 0.5 + 20 });
  const moved = (await box.boundingBox())!;
  expect(moved.x - before.x).toBeGreaterThan(30);
  expect(moved.y - before.y).toBeGreaterThan(10);
  expect(Math.abs(moved.width - before.width)).toBeLessThan(2); // moving does not resize

  // Resize from the south-east handle, which must be big enough for a fingertip.
  const handle = page.getByTestId("handle-se");
  await expect(handle).toBeVisible();
  const handleBox = (await handle.boundingBox())!;
  expect(handleBox.width).toBeGreaterThanOrEqual(20);
  await touchDrag(page, at(handleBox, 0.5, 0.5), { x: handleBox.x + handleBox.width / 2 + 30, y: handleBox.y + handleBox.height / 2 + 20 });
  const resized = (await box.boundingBox())!;
  expect(resized.width - moved.width).toBeGreaterThan(20);
  expect(resized.height - moved.height).toBeGreaterThan(10);
  expect(Math.abs(resized.x - moved.x)).toBeLessThan(2); // the opposite corner stays put

  // A box must stay inside the image even if the finger leaves it.
  const bounds = await canvasBounds(page);
  const edge = (await box.boundingBox())!;
  await touchDrag(page, at(edge, 0.5, 0.5), { x: bounds.x + bounds.width + 200, y: bounds.y + bounds.height + 200 });
  const clamped = (await box.boundingBox())!;
  expect(clamped.x + clamped.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
  expect(clamped.y + clamped.height).toBeLessThanOrEqual(bounds.y + bounds.height + 1);

  // Rename without a double-click, which a touchscreen can't do reliably.
  await page.getByRole("button", { name: "Rename car box" }).tap();
  const rename = page.getByLabel("Entity name");
  await expect(rename).toHaveValue("car");
  await rename.fill("truck");
  await rename.press("Enter");
  await expect(box).toHaveAttribute("aria-label", "truck box");

  // Submit with a tap.
  await page.getByRole("button", { name: /Submit/ }).tap();
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible();
});

test("a stray tap on the image does not create a box", async ({ page, request }) => {
  await openLabelPage(page, request);
  const canvas = await canvasBounds(page);
  await page.touchscreen.tap(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
  await expect(page.getByLabel("Entity name")).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Boxes" }).getByRole("listitem")).toHaveCount(1); // "No boxes yet"
  await expect(page.getByText("No boxes yet")).toBeVisible();
});
