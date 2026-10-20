import { deflateSync, crc32 } from "node:zlib";
import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";

export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

const chunk = (type: string, data: Buffer): Buffer => {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
};

/** A valid, unique-per-seed RGB PNG (different seeds hash differently). */
export const makePng = (seed: number, width = 160, height = 100): Buffer => {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // RGB
  const row = Buffer.alloc(1 + width * 3);
  const raw = Buffer.alloc(row.length * height);
  for (let y = 0; y < height; y += 1) {
    const offset = y * row.length;
    raw[offset] = 0; // filter: none
    for (let x = 0; x < width; x += 1) {
      raw[offset + 1 + x * 3] = (seed * 37 + x) % 256;
      raw[offset + 2 + x * 3] = (seed * 91 + y) % 256;
      raw[offset + 3 + x * 3] = (seed * 17 + x + y) % 256;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
};

export const uniqueSeed = (): number => Math.floor(Math.random() * 1_000_000);

export const devToken = async (
  request: APIRequestContext,
  email: string,
  roles: string[],
): Promise<string> => {
  const res = await request.post(`${API_URL}/dev/login`, { data: { email, roles } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { token: string }).token;
};

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

/** Creates a batch with `count` ready (verified, de-duplicated) images, through the real API + S3. */
export const seedBatch = async (
  request: APIRequestContext,
  token: string,
  count: number,
): Promise<string> => {
  const created = await request.post(`${API_URL}/batches`, {
    data: { name: `e2e-${Date.now()}` },
    headers: authHeaders(token),
  });
  expect(created.status()).toBe(201);
  const batchId = ((await created.json()) as { id: string }).id;

  const base = uniqueSeed();
  const pngs = Array.from({ length: count }, (_, i) => makePng(base + i));
  const reg = await request.post(`${API_URL}/batches/${batchId}/uploads`, {
    data: {
      files: pngs.map((p, i) => ({ filename: `seed-${i}.png`, content_type: "image/png", size: p.length })),
    },
    headers: authHeaders(token),
  });
  const { tickets } = (await reg.json()) as {
    tickets: { image_id: string; upload_url: string; headers: Record<string, string> }[];
  };
  for (const [i, ticket] of tickets.entries()) {
    const put = await request.put(ticket.upload_url, { data: pngs[i], headers: ticket.headers });
    expect(put.ok()).toBeTruthy();
  }
  const done = await request.post(`${API_URL}/batches/${batchId}/uploads/complete`, {
    data: { image_ids: tickets.map((t) => t.image_id) },
    headers: authHeaders(token),
  });
  const { results } = (await done.json()) as { results: { result: string }[] };
  expect(results.every((r) => r.result === "ready")).toBe(true);
  return batchId;
};

export const signIn = async (page: Page, email: string, roles: string[]): Promise<void> => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  for (const role of ["labeler", "reviewer", "admin"]) {
    await page.getByRole("checkbox", { name: role }).setChecked(roles.includes(role));
  }
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("link", { name: "Data Labeler" })).toBeVisible();
};

/** Waits for the labeling/review image to be decoded, then returns the canvas bounds. */
export const canvasBounds = async (page: Page) => {
  const canvas = page.getByTestId("canvas");
  await expect(canvas).toBeVisible();
  await expect
    .poll(() => canvas.locator("img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
    .toBe(true);
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas has no bounding box");
  return box;
};

/** Drag a box using fractional coordinates of the canvas, then name it. */
export const drawBox = async (
  page: Page,
  from: [number, number],
  to: [number, number],
  label: string,
): Promise<void> => {
  const b = await canvasBounds(page);
  await page.mouse.move(b.x + b.width * from[0], b.y + b.height * from[1]);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width * ((from[0] + to[0]) / 2), b.y + b.height * ((from[1] + to[1]) / 2), {
    steps: 4,
  });
  await page.mouse.move(b.x + b.width * to[0], b.y + b.height * to[1], { steps: 4 });
  await page.mouse.up();
  const input = page.getByLabel("Entity name");
  await expect(input).toBeVisible();
  await input.fill(label);
  await input.press("Enter");
  await expect(page.getByLabel(`${label} box`).first()).toBeVisible();
};
