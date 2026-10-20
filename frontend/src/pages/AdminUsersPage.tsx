import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api/client";
import type { AdminUser, Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const ROLES: Role[] = ["admin", "labeler", "reviewer"];

const toggle = (roles: Role[], role: Role): Role[] =>
  roles.includes(role) ? roles.filter((r) => r !== role) : [...roles, role];

const RoleChecks = ({
  roles,
  onChange,
  disabled,
  label,
}: {
  roles: Role[];
  onChange: (next: Role[]) => void;
  disabled?: boolean;
  label: string;
}) => (
  <span role="group" aria-label={label}>
    {ROLES.map((role) => (
      <label key={role} style={{ marginRight: "0.75rem" }}>
        <input
          type="checkbox"
          checked={roles.includes(role)}
          disabled={disabled}
          onChange={() => onChange(toggle(roles, role))}
        />{" "}
        {role}
      </label>
    ))}
  </span>
);

/** Admin: invite users, change roles, disable or re-enable accounts. */
export const AdminUsersPage = () => {
  const { identity } = useAuth();
  const [email, setEmail] = useState("");
  const [inviteRoles, setInviteRoles] = useState<Role[]>(["labeler"]);
  const [extraPages, setExtraPages] = useState<AdminUser[]>([]);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  // Results of this session's changes, applied on top of whatever pages are loaded. Reloading the
  // list after a change would drop the user back to page one, and a user found on page three
  // would vanish from under the cursor.
  const [changed, setChanged] = useState<Record<string, AdminUser>>({});
  const [invited, setInvited] = useState<AdminUser[]>([]);

  const isAdmin = !!identity?.roles.includes("admin");
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api.listAdminUsers(), enabled: isAdmin });

  const remember = (user: AdminUser) => setChanged((prev) => ({ ...prev, [user.sub]: user }));

  const invite = useMutation({
    mutationFn: () => api.inviteUser(email.trim(), inviteRoles),
    onSuccess: (user) => {
      setEmail("");
      setInvited((prev) => [user, ...prev.filter((u) => u.sub !== user.sub)]);
      remember(user);
    },
  });
  const setRoles = useMutation({
    mutationFn: ({ sub, roles }: { sub: string; roles: Role[] }) => api.setUserRoles(sub, roles),
    onSuccess: remember,
  });
  const setEnabled = useMutation({
    mutationFn: ({ sub, enabled }: { sub: string; enabled: boolean }) => api.setUserEnabled(sub, enabled),
    onSuccess: remember,
  });

  if (identity && !isAdmin) return <Navigate to="/" replace />;

  const first = users.data?.users ?? [];
  const more = nextToken ?? (extraPages.length === 0 ? (users.data?.next_token ?? null) : null);
  const merged = new Map<string, AdminUser>();
  for (const u of [...invited, ...first, ...extraPages]) {
    if (!merged.has(u.sub)) merged.set(u.sub, changed[u.sub] ?? u);
  }
  const rows = [...merged.values()];

  const loadMore = async () => {
    if (!more) return;
    setLoadingMore(true);
    try {
      const page = await api.listAdminUsers(more);
      setExtraPages((prev) => [...prev, ...page.users]);
      setNextToken(page.next_token);
    } finally {
      setLoadingMore(false);
    }
  };

  const onInvite = (e: FormEvent) => {
    e.preventDefault();
    if (email.trim()) invite.mutate();
  };

  const error = invite.error ?? setRoles.error ?? setEnabled.error ?? users.error;
  const busy = setRoles.isPending || setEnabled.isPending;

  return (
    <div className="page">
      <h1>Users</h1>
      <p className="muted">
        Invited users receive a temporary password by email. Role changes and disabling take effect on the user&apos;s
        next request.
      </p>

      <form className="card" onSubmit={onInvite} aria-label="Invite user">
        <h2>Invite a user</h2>
        <div className="row">
          <input
            type="email"
            aria-label="Email"
            placeholder="name@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <RoleChecks roles={inviteRoles} onChange={setInviteRoles} label="Roles for the new user" />
          <button type="submit" disabled={!email.trim() || invite.isPending}>
            Send invite
          </button>
        </div>
      </form>

      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}

      {users.isLoading && <p>Loading…</p>}
      <table className="table" data-testid="users-table">
        <thead>
          <tr>
            <th>Email</th>
            <th>Roles</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => {
            const isSelf = u.sub === identity?.sub;
            return (
              <tr key={u.sub} data-testid={`user-${u.email}`}>
                <td>
                  {u.email}
                  {isSelf && <span className="badge">you</span>}
                </td>
                <td>
                  <RoleChecks
                    label={`Roles for ${u.email}`}
                    roles={u.roles}
                    disabled={busy}
                    onChange={(next) => setRoles.mutate({ sub: u.sub, roles: next })}
                  />
                </td>
                <td>{u.enabled ? u.status.toLowerCase().replace(/_/g, " ") || "active" : "disabled"}</td>
                <td>
                  <button
                    className={u.enabled ? "danger" : undefined}
                    disabled={busy || (isSelf && u.enabled)}
                    onClick={() => setEnabled.mutate({ sub: u.sub, enabled: !u.enabled })}
                  >
                    {u.enabled ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            );
          })}
          {users.data && rows.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                No users yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {more && (
        <button className="secondary" onClick={() => void loadMore()} disabled={loadingMore}>
          Load more
        </button>
      )}
    </div>
  );
};
