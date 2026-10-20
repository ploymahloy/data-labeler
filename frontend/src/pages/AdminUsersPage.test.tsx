import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminUser } from "../api/types";
import { AuthProvider } from "../auth/AuthContext";
import { saveSession } from "../auth/sessionStore";
import { makeToken } from "../test/fakeAuthServer";
import { AdminUsersPage } from "./AdminUsersPage";

const user = (email: string, roles: AdminUser["roles"], enabled = true, sub = `sub-${email}`): AdminUser => ({
  sub,
  email,
  roles,
  enabled,
  status: "CONFIRMED",
  created_at: null,
});

/** A small in-memory admin API behind fetch, with the same guards the real one enforces. */
const installAdminApi = (initial: AdminUser[], pageSize = Number.POSITIVE_INFINITY) => {
  const users = [...initial];
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path: url.pathname, body });
      const ok = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
      const err = (status: number, code: string, message: string) => ok({ detail: { code, message } }, status);

      if (method === "GET" && url.pathname === "/admin/users") {
        const start = Number(url.searchParams.get("token") ?? 0);
        const end = start + pageSize;
        return ok({ users: users.slice(start, end), next_token: end < users.length ? String(end) : null });
      }
      if (method === "POST" && url.pathname === "/admin/users") {
        if (users.some((u) => u.email === body.email)) return err(409, "user_exists", "A user with that email already exists");
        const created = user(body.email, body.roles);
        users.push(created);
        return ok(created, 201);
      }
      const match = /^\/admin\/users\/([^/]+)\/(roles|disable|enable)$/.exec(url.pathname);
      if (match) {
        const target = users.find((u) => u.sub === decodeURIComponent(match[1] ?? ""));
        if (!target) return err(404, "not_found", "User not found");
        if (match[2] === "roles") target.roles = body.roles;
        if (match[2] === "disable") target.enabled = false;
        if (match[2] === "enable") target.enabled = true;
        return ok(target);
      }
      return err(500, "unexpected", `${method} ${url.pathname}`);
    }),
  );
  return { users, calls };
};

const mount = (roles: string[]) => {
  saveSession({ idToken: makeToken(3600, { sub: "sub-admin@example.com", email: "admin@example.com", "cognito:groups": roles }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/admin/users"]}>
          <Routes>
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/" element={<p>home</p>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
};

describe("AdminUsersPage", () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => {
    saveSession(null);
    vi.unstubAllGlobals();
  });

  it("sends non-admins home without calling the API", async () => {
    const { calls } = installAdminApi([]);
    mount(["labeler"]);
    expect(await screen.findByText("home")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("lists users with their roles and status", async () => {
    installAdminApi([user("admin@example.com", ["admin"]), user("ana@example.com", ["labeler"], false)]);
    mount(["admin"]);
    const row = await screen.findByTestId("user-ana@example.com");
    expect(within(row).getByRole("checkbox", { name: "labeler" })).toBeChecked();
    expect(within(row).getByRole("checkbox", { name: "reviewer" })).not.toBeChecked();
    expect(within(row).getByText("disabled")).toBeInTheDocument();
  });

  it("changes roles through the API and shows the new set", async () => {
    const { calls } = installAdminApi([user("admin@example.com", ["admin"]), user("ana@example.com", ["labeler"])]);
    mount(["admin"]);
    const row = await screen.findByTestId("user-ana@example.com");

    await userEvent.click(within(row).getByRole("checkbox", { name: "reviewer" }));

    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "PUT",
        path: "/admin/users/sub-ana%40example.com/roles",
        body: { roles: ["labeler", "reviewer"] },
      }),
    );
    await waitFor(() =>
      expect(within(screen.getByTestId("user-ana@example.com")).getByRole("checkbox", { name: "reviewer" })).toBeChecked(),
    );
  });

  it("keeps a user from a later page on screen after changing them", async () => {
    installAdminApi(
      [user("admin@example.com", ["admin"]), user("ana@example.com", ["labeler"]), user("zed@example.com", ["labeler"])],
      2,
    );
    mount(["admin"]);
    await screen.findByTestId("user-ana@example.com");
    expect(screen.queryByTestId("user-zed@example.com")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    const row = await screen.findByTestId("user-zed@example.com");
    await userEvent.click(within(row).getByRole("checkbox", { name: "reviewer" }));

    // Not bounced back to page one: the same row is still there, now with the new role.
    await waitFor(() =>
      expect(within(screen.getByTestId("user-zed@example.com")).getByRole("checkbox", { name: "reviewer" })).toBeChecked(),
    );
    expect(screen.getByTestId("user-ana@example.com")).toBeInTheDocument();
  });

  it("shows a newly invited user straight away, even if they sort onto a later page", async () => {
    installAdminApi([user("admin@example.com", ["admin"]), user("ana@example.com", ["labeler"])], 1);
    mount(["admin"]);
    await screen.findByTestId("user-admin@example.com");

    await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invite" }));

    expect(await screen.findByTestId("user-new@example.com")).toBeInTheDocument();
  });

  it("disables and re-enables a user", async () => {
    installAdminApi([user("admin@example.com", ["admin"]), user("ana@example.com", ["labeler"])]);
    mount(["admin"]);
    const row = await screen.findByTestId("user-ana@example.com");

    await userEvent.click(within(row).getByRole("button", { name: "Disable" }));
    expect(await within(await screen.findByTestId("user-ana@example.com")).findByRole("button", { name: "Enable" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId("user-ana@example.com")).getByRole("button", { name: "Enable" }));
    expect(await within(await screen.findByTestId("user-ana@example.com")).findByRole("button", { name: "Disable" })).toBeInTheDocument();
  });

  it("does not offer to disable yourself", async () => {
    installAdminApi([user("admin@example.com", ["admin"])]);
    mount(["admin"]);
    const row = await screen.findByTestId("user-admin@example.com");
    expect(within(row).getByRole("button", { name: "Disable" })).toBeDisabled();
    expect(within(row).getByText("you")).toBeInTheDocument();
  });

  it("invites a user with the chosen roles and shows API errors", async () => {
    const { calls } = installAdminApi([user("admin@example.com", ["admin"])]);
    mount(["admin"]);
    await screen.findByTestId("user-admin@example.com");

    const form = screen.getByRole("form", { name: "Invite user" });
    await userEvent.type(within(form).getByLabelText("Email"), "new@example.com");
    await userEvent.click(within(form).getByRole("checkbox", { name: "reviewer" }));
    await userEvent.click(within(form).getByRole("button", { name: "Send invite" }));

    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "POST",
        path: "/admin/users",
        body: { email: "new@example.com", roles: ["labeler", "reviewer"] },
      }),
    );
    expect(await screen.findByTestId("user-new@example.com")).toBeInTheDocument();

    await userEvent.type(within(form).getByLabelText("Email"), "new@example.com");
    await userEvent.click(within(form).getByRole("button", { name: "Send invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});
