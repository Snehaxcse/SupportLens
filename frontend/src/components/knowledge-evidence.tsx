"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import type { Schemas } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import { Button, Loading, Notice } from "./ui";

export function SourceLink({ url }: { url: string | null }) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password)
      return null;
    return (
      <a
        className="text-link"
        href={parsed.href}
        target="_blank"
        rel="noopener noreferrer"
      >
        Open source
      </a>
    );
  } catch {
    return null;
  }
}

const scores: Record<string, string> = {
  POSTGRES_TEXT_RANK: "Text rank",
  COSINE_SIMILARITY: "Cosine similarity",
  RECIPROCAL_RANK_FUSION: "Rank fusion",
};

export function MethodSelect({
  value,
  onChange,
}: {
  value: Schemas["Method"];
  onChange: (value: Schemas["Method"]) => void;
}) {
  return (
    <label className="knowledge-field">
      Retrieval method
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as Schemas["Method"])}
      >
        <option value="hybrid">Hybrid</option>
        <option value="lexical">Lexical</option>
        <option value="semantic">Semantic</option>
      </select>
    </label>
  );
}

export function EvidenceList({ data }: { data: Schemas["RetrievalResult"] }) {
  return (
    <>
      {data.method !== data.requested_method && (
        <Notice title="Showing lexical results">
          Semantic evidence is unavailable for this search.
        </Notice>
      )}
      {data.method === data.requested_method &&
        !["AVAILABLE", "NOT_REQUESTED"].includes(data.semantic_state) && (
          <Notice title="Semantic evidence unavailable">
            Try a lexical search or check the article index status.
          </Notice>
        )}
      {data.items.length === 0 ? (
        <p className="field-help">No matching published evidence.</p>
      ) : (
        <ol className="knowledge-results">
          {data.items.map((item) => (
            <li key={item.chunk_id}>
              <Link
                className="text-link"
                href={`/agent/knowledge/${item.article_id}?revision=${item.revision}`}
              >
                {item.title}
              </Link>
              <p className="field-help">
                {item.source_type === "SYNTHETIC_TEST" && "Synthetic test · "}
                {item.source_name} · Revision {item.revision}
              </p>
              <p className="knowledge-excerpt">{item.excerpt}</p>
              <p className="field-help">
                {scores[item.score_type] || "Ranking score"}{" "}
                {item.score.toFixed(3)}
              </p>
              <SourceLink url={item.source_url} />
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function TicketEvidence({
  ticketId,
  method,
}: {
  ticketId: string;
  method: Schemas["Method"];
}) {
  const { api } = useAuth();
  const load = useCallback(
    (signal: AbortSignal) => api.ticketKnowledge(ticketId, method, signal),
    [api, ticketId, method],
  );
  const evidence = useResource(`knowledge:${ticketId}:${method}`, load);
  return (
    <>
      {evidence.loading && <Loading label="Finding relevant evidence…" />}
      {evidence.error && (
        <Notice kind="error" title="Could not retrieve evidence">
          {evidence.error.message}
        </Notice>
      )}
      {!evidence.loading && !evidence.error && evidence.data && (
        <EvidenceList data={evidence.data} />
      )}
      <Button
        type="button"
        variant="quiet"
        disabled={evidence.loading}
        onClick={evidence.reload}
      >
        Refresh evidence
      </Button>
    </>
  );
}

export function TicketKnowledgePanel({ ticketId }: { ticketId: string }) {
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<Schemas["Method"]>("hybrid");
  return (
    <section
      className="card knowledge-card"
      aria-labelledby="knowledge-evidence-title"
    >
      <h2 id="knowledge-evidence-title">Relevant knowledge</h2>
      <p className="field-help">
        Review the retrieved evidence and its source before using it in a reply.
      </p>
      {open ? (
        <>
          <MethodSelect value={method} onChange={setMethod} />
          <TicketEvidence ticketId={ticketId} method={method} />
        </>
      ) : (
        <Button type="button" variant="secondary" onClick={() => setOpen(true)}>
          Find evidence
        </Button>
      )}
    </section>
  );
}
