import { expect, test } from "@playwright/test";
import { API_URL, devToken, seedBatch } from "./helpers";

type Claimed = { id: string };

test("two users draining a queue at the same time never receive the same image", async ({ request }) => {
  const total = 14;
  const owner = await devToken(request, `owner-${Date.now()}@example.com`, ["labeler"]);
  const batchId = await seedBatch(request, owner, total);

  const work = async (email: string): Promise<string[]> => {
    const token = await devToken(request, email, ["labeler"]);
    const headers = { Authorization: `Bearer ${token}` };
    const mine: string[] = [];
    for (;;) {
      const res = await request.post(`${API_URL}/batches/${batchId}/images/next`, { headers });
      if (res.status() === 204) return mine;
      expect(res.status()).toBe(200);
      const { id } = (await res.json()) as Claimed;
      mine.push(id);
      const submitted = await request.post(`${API_URL}/images/${id}/submit`, {
        data: { annotations: [] },
        headers,
      });
      expect(submitted.status()).toBe(200);
    }
  };

  const stamp = Date.now();
  const [a, b] = await Promise.all([work(`ann-a-${stamp}@example.com`), work(`ann-b-${stamp}@example.com`)]);

  expect(a.length + b.length).toBe(total);
  expect(new Set([...a, ...b]).size).toBe(total); // every image handled exactly once
  expect(a.filter((id) => b.includes(id))).toEqual([]); // and never by both users
});

test("a second user cannot write to an image leased by someone else", async ({ request }) => {
  const stamp = Date.now();
  const owner = await devToken(request, `owner-${stamp}@example.com`, ["labeler"]);
  const batchId = await seedBatch(request, owner, 1);
  const holder = { Authorization: `Bearer ${await devToken(request, `holder-${stamp}@example.com`, ["labeler"])}` };
  const other = { Authorization: `Bearer ${await devToken(request, `other-${stamp}@example.com`, ["labeler"])}` };

  const claimed = (await (await request.post(`${API_URL}/batches/${batchId}/images/next`, { headers: holder })).json()) as Claimed;
  const intruder = await request.post(`${API_URL}/images/${claimed.id}/submit`, {
    data: { annotations: [] },
    headers: other,
  });
  expect(intruder.status()).toBe(409);
  const none = await request.post(`${API_URL}/batches/${batchId}/images/next`, { headers: other });
  expect(none.status()).toBe(204);
});
