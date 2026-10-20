import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Batch } from "../api/types";
import { BatchAdminPanel } from "./BatchAdminPanel";

const batch = (overrides: Partial<Batch> = {}): Batch => ({
  id: "b1",
  name: "Street scenes",
  status: "open",
  created_at: "2026-01-01T00:00:00Z",
  created_by: "u1",
  archived_at: null,
  counts: {},
  ...overrides,
});

type Call = { method: string; path: string };

/** A tiny in-memory API: records calls and answers from a queue per "METHOD path". */
const installApi = (responses: Record<string, Array<{ status?: number; body?: unknown }>>) => {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = new URL(input).pathname;
      const method = init?.method ?? "GET";
      calls.push({ method, path });
      const next = responses[`${method} ${path}`]?.shift() ?? { status: 500, body: { detail: "unexpected" } };
      return new Response(JSON.stringify(next.body ?? {}), {
        status: next.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
};

const mount = (b: Batch, handlers: { onChanged?: () => void; onDeleted?: () => void } = {}) => {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BatchAdminPanel batch={b} onChanged={handlers.onChanged ?? vi.fn()} onDeleted={handlers.onDeleted ?? vi.fn()} />
    </QueryClientProvider>,
  );
};

afterEach(() => vi.unstubAllGlobals());

describe("BatchAdminPanel", () => {
  it("archives only after an explicit confirmation", async () => {
    const calls = installApi({ "POST /batches/b1/archive": [{ body: batch({ status: "archived" }) }] });
    const onChanged = vi.fn();
    mount(batch(), { onChanged });

    await userEvent.click(screen.getByRole("button", { name: "Archive batch" }));
    expect(calls).toHaveLength(0); // nothing sent yet
    expect(screen.getByRole("alertdialog", { name: "Confirm archive" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls).toEqual([{ method: "POST", path: "/batches/b1/archive" }]);
  });

  it("offers unarchive and delete, but not archive, for an archived batch", () => {
    installApi({});
    mount(batch({ status: "archived", archived_at: "2026-02-01T00:00:00Z" }));
    expect(screen.getByRole("button", { name: "Unarchive" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete permanently/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive batch" })).not.toBeInTheDocument();
  });

  it("never shows delete for an open batch", () => {
    installApi({});
    mount(batch());
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("requires typing the batch name, then deletes in several calls and reports progress", async () => {
    const calls = installApi({
      "DELETE /batches/b1": [
        { body: { done: false, objects_deleted: 1000, images_deleted: 0 } },
        { body: { done: false, objects_deleted: 0, images_deleted: 1000 } },
        { body: { done: true, objects_deleted: 0, images_deleted: 20 } },
      ],
    });
    const onDeleted = vi.fn();
    mount(batch({ status: "archived", archived_at: "2026-02-01T00:00:00Z" }), { onDeleted });

    await userEvent.click(screen.getByRole("button", { name: /Delete permanently/ }));
    const confirm = screen.getByRole("button", { name: "Delete forever" });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Batch name"), "Street");
    expect(confirm).toBeDisabled(); // partial name is not enough
    await userEvent.type(screen.getByLabelText("Batch name"), " scenes");
    expect(confirm).toBeEnabled();

    await userEvent.click(confirm);
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(3);
    expect(screen.getByTestId("delete-progress")).toHaveTextContent("Removed 1000 files and 1020 images");
  });

  it("explains the cooldown when delete is refused right after archiving", async () => {
    installApi({
      "DELETE /batches/b1": [
        {
          status: 409,
          body: { detail: { code: "archive_cooldown", message: "wait", retry_after_seconds: 240 } },
        },
      ],
    });
    const onDeleted = vi.fn();
    mount(batch({ status: "archived", archived_at: "2026-02-01T00:00:00Z" }), { onDeleted });

    await userEvent.click(screen.getByRole("button", { name: /Delete permanently/ }));
    await userEvent.type(screen.getByLabelText("Batch name"), "Street scenes");
    await userEvent.click(screen.getByRole("button", { name: "Delete forever" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 240 seconds");
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
