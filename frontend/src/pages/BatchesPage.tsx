import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { hasRole } from "../auth/jwt";

export const BatchesPage = () => {
  const { identity } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const isAdmin = !!identity?.roles.includes("admin");

  const batches = useQuery({
    queryKey: ["batches", showArchived],
    queryFn: () => api.listBatches(showArchived),
  });
  const create = useMutation({
    mutationFn: (batchName: string) => api.createBatch(batchName),
    onSuccess: async (batch) => {
      await queryClient.invalidateQueries({ queryKey: ["batches"] });
      navigate(`/batches/${batch.id}/upload`);
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  return (
    <div className="page">
      <h1>Batches</h1>

      {hasRole(identity, "labeler") && (
        <form className="row" onSubmit={onSubmit}>
          <input
            aria-label="New batch name"
            placeholder="New batch name"
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
          />
          <button type="submit" disabled={!name.trim() || create.isPending}>
            Create batch
          </button>
        </form>
      )}
      {isAdmin && (
        <label>
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show
          archived batches
        </label>
      )}
      {create.isError && (
        <p className="error" role="alert">
          {create.error.message}
        </p>
      )}

      {batches.isLoading && <p>Loading…</p>}
      {batches.isError && (
        <p className="error" role="alert">
          {batches.error.message}
        </p>
      )}
      {batches.data && batches.data.length === 0 && <p className="muted">No batches yet.</p>}
      <ul className="batch-list">
        {batches.data?.map((b) => {
          const total = Object.values(b.counts).reduce((a, n) => a + (n ?? 0), 0);
          const done = b.counts.accepted ?? 0;
          return (
            <li key={b.id} className="card">
              <Link to={`/batches/${b.id}`}>
                <strong>{b.name}</strong>
              </Link>
              {b.status === "archived" && <span className="badge"> Archived</span>}
              <div className="muted">
                {done} / {total} accepted · created {new Date(b.created_at).toLocaleDateString()}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
};
