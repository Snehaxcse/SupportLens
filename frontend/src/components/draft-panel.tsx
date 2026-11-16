"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Schemas } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import { Button, ConfirmAction, Loading, Notice } from "./ui";

type Run = Schemas["DraftRun"];
type Action = Schemas["ReviewDraft"]["action"];

const labels: Record<Run["state"], string> = {
  PENDING: "Draft queued",
  RUNNING: "Generating draft",
  DRAFT: "Ready for review",
  APPROVED: "Approved for agent reference",
  REJECTED: "Rejected",
  ABSTAINED: "Manual reply needed",
  FAILED: "Draft unavailable",
};

const failures: Record<string, string> = {
  INSUFFICIENT_EVIDENCE: "No usable published evidence supports a draft.",
  EVIDENCE_CHANGED:
    "The evidence changed. Refresh or regenerate with current sources.",
  SENSITIVE_CASE: "A human agent should handle this sensitive request.",
  PROMPT_INJECTION:
    "The ticket contains instructions aimed at the drafting system.",
  MODEL_ESCALATION: "The draft requires manual handling.",
  INVALID_GROUNDING: "The proposed evidence references could not be validated.",
  PROVIDER_QUOTA:
    "Gemini's free-tier quota is exhausted. Continue with a manual reply.",
  DAILY_LIMIT:
    "SupportLens reached its daily generation limit. Continue with a manual reply.",
  PROVIDER_DISABLED: "Gemini drafting is disabled for this ticket.",
  INTERRUPTED: "Generation was interrupted. Refresh before trying again.",
};

function DraftContent({ run }: { run: Run }) {
  return (
    <>
      {run.synthetic && (
        <Notice title="Synthetic verification draft">
          For internal testing only.
        </Notice>
      )}
      <p role="status" className="knowledge-status">
        {labels[run.state]}
      </p>
      {run.failure && (
        <p className="field-help">
          {failures[run.failure] ??
            "Generation could not finish. Continue with a manual reply."}
        </p>
      )}
      {run.text && <p className="knowledge-body">{run.text}</p>}
      {run.evidence.length > 0 && (
        <details>
          <summary>Evidence ({run.evidence.length})</summary>
          <ul className="knowledge-results">
            {run.evidence.map((item) => (
              <li key={item.chunk_id}>
                <Link
                  className="text-link"
                  href={`/agent/knowledge/${item.article_id}?revision=${item.revision}`}
                >
                  {item.title}
                </Link>
                <p className="field-help">
                  {item.source_name} · Revision {item.revision}
                </p>
                <p className="knowledge-excerpt">{item.excerpt}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="field-help">
        {run.model} · {run.prompt_version}
      </p>
    </>
  );
}

function DraftControls({ ticketId }: { ticketId: string }) {
  const { api, announce } = useAuth();
  const [page, setPage] = useState(1);
  const [edit, setEdit] = useState<{ id: string; text: string }>();
  const load = useCallback(
    (signal: AbortSignal) => api.drafts(ticketId, page, signal),
    [api, ticketId, page],
  );
  const resource = useResource(`drafts:${ticketId}:${page}`, load);
  const mutation = useMutation();
  const activation = useRef<{ key: string; id: string } | undefined>(undefined);
  const data = resource.data;
  const current = page === 1 ? data?.items[0] : undefined;
  const active = current?.state === "PENDING" || current?.state === "RUNNING";
  const editing = !!edit && edit.id === current?.id;
  useEffect(() => {
    if (!active || resource.loading || resource.error) return;
    const timer = window.setTimeout(resource.reload, 3500);
    return () => window.clearTimeout(timer);
  }, [active, resource.loading, resource.error, resource.reload]);

  function requestId(key: string) {
    if (activation.current?.key !== key)
      activation.current = { key, id: crypto.randomUUID() };
    return activation.current.id;
  }

  async function generate() {
    const parent = current?.id ?? null;
    const run = await mutation.run(() =>
      api.generateDraft(ticketId, {
        expected_run_id: parent,
        request_id: requestId(`generate:${parent}`),
      }),
    );
    if (run) {
      activation.current = undefined;
      setEdit(undefined);
      resource.reload();
    }
  }

  async function review(action: Action, text?: string) {
    if (!current || resource.loading || resource.error) return false;
    const key = JSON.stringify([current.id, current.version, action, text]);
    const run = await mutation.run(() =>
      api.reviewDraft(ticketId, current.id, {
        action,
        expected_version: current.version,
        request_id: requestId(key),
        ...(text === undefined ? {} : { text }),
      }),
    );
    if (!run) return false;
    activation.current = undefined;
    setEdit(undefined);
    announce(
      action === "APPROVE"
        ? "Draft approved for internal reference."
        : action === "REJECT"
          ? "Draft rejected."
          : "Draft edit saved.",
    );
    resource.reload();
    return true;
  }

  const disabled = mutation.busy || resource.loading || !!resource.error;
  return (
    <>
      {resource.loading && !data && <Loading label="Loading draft history…" />}
      {resource.error && (
        <Notice title="Could not load drafts" kind="error">
          {resource.error.message}
        </Notice>
      )}
      {mutation.error && (
        <Notice title="Draft action needs attention" kind="error">
          {mutation.error.message}
        </Notice>
      )}
      {current && <DraftContent run={current} />}
      {data && !resource.error && !data.items.length && (
        <p className="field-help">No draft attempts yet.</p>
      )}
      {data && !data.generation_enabled && (
        <p className="field-help">
          AI drafting is unavailable for this ticket. You can still reply
          manually.
        </p>
      )}
      {current?.state === "DRAFT" &&
        !resource.error &&
        (editing ? (
          <form
            className="knowledge-form"
            onSubmit={(event) => {
              event.preventDefault();
              void review("EDIT", edit.text);
            }}
          >
            <label className="knowledge-field">
              Draft text
              <textarea
                rows={6}
                maxLength={1600}
                required
                value={edit.text}
                onChange={(event) =>
                  setEdit({ id: current.id, text: event.target.value })
                }
              />
            </label>
            <p className="field-help">
              Keep the opening. You can remove or rearrange complete evidence
              lines.
            </p>
            <div className="knowledge-actions">
              <Button disabled={disabled}>Save edit</Button>
              <Button
                type="button"
                variant="quiet"
                disabled={mutation.busy}
                onClick={() => setEdit(undefined)}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <div className="knowledge-actions">
            <Button
              variant="secondary"
              disabled={disabled}
              onClick={() =>
                setEdit({ id: current.id, text: current.text ?? "" })
              }
            >
              Edit draft
            </Button>
            <Button
              variant="secondary"
              disabled={disabled}
              onClick={() => review("REJECT")}
            >
              Reject draft
            </Button>
            <ConfirmAction
              label="Approve draft"
              title="Approve for agent reference?"
              disabled={disabled}
              onConfirm={() => review("APPROVE")}
            >
              Approval records your review internally. A customer reply requires
              a separate send action.
            </ConfirmAction>
          </div>
        ))}
      <div className="knowledge-actions">
        {page === 1 && data?.generation_enabled && (
          <Button
            variant="secondary"
            disabled={disabled || active || editing}
            onClick={generate}
          >
            {current ? "Regenerate draft" : "Generate draft"}
          </Button>
        )}
        <Button
          variant="quiet"
          disabled={resource.loading || mutation.busy}
          onClick={resource.reload}
        >
          Refresh drafts
        </Button>
      </div>
      {data && data.total > 1 && (
        <details>
          <summary>Draft history ({data.total})</summary>
          {data.items
            .filter((run) => run.id !== current?.id)
            .map((run) => (
              <details key={run.id}>
                <summary>
                  {labels[run.state]} ·{" "}
                  {new Date(run.created_at).toLocaleString()}
                </summary>
                <DraftContent run={run} />
              </details>
            ))}
          <div className="knowledge-actions">
            <Button
              variant="quiet"
              disabled={disabled || page <= 1}
              onClick={() => setPage(page - 1)}
            >
              Newer attempts
            </Button>
            <Button
              variant="quiet"
              disabled={disabled || page * 10 >= data.total}
              onClick={() => setPage(page + 1)}
            >
              Older attempts
            </Button>
          </div>
        </details>
      )}
    </>
  );
}

export function DraftPanel({ ticketId }: { ticketId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card knowledge-card" aria-labelledby="ai-draft-title">
      <h2 id="ai-draft-title">AI draft</h2>
      <p className="field-help">
        Generate evidence-based guidance for agent review.
      </p>
      {open ? (
        <DraftControls key={ticketId} ticketId={ticketId} />
      ) : (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Open AI drafts
        </Button>
      )}
    </section>
  );
}
