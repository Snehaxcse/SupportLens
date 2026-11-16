"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { Schemas } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import { date } from "@/lib/tickets";
import { EvidenceList, MethodSelect, SourceLink } from "./knowledge-evidence";
import {
  Button,
  ButtonLink,
  ConfirmAction,
  Empty,
  Loading,
  Notice,
  PageHeading,
} from "./ui";

const sources = {
  APPROVED_REFERENCE: "Approved reference",
  INTERNAL_PROCEDURE: "Internal procedure",
  SYNTHETIC_TEST: "Synthetic test",
} satisfies Record<Schemas["SourceType"], string>;

function SourceChoices() {
  return Object.entries(sources).map(([value, label]) => (
    <option key={value} value={value}>
      {label}
    </option>
  ));
}

const indexLabels: Record<string, string> = {
  IDLE: "Not indexed",
  PENDING: "Queued for indexing",
  RUNNING: "Indexing",
  READY: "Indexed for lexical and semantic retrieval",
  LEXICAL_ONLY: "Indexed for lexical retrieval only",
  FAILED: "Indexing failed",
};

function Preview() {
  const { api } = useAuth();
  const mutation = useMutation();
  const [method, setMethod] = useState<Schemas["Method"]>("hybrid");
  const [data, setData] = useState<Schemas["RetrievalResult"]>();
  return (
    <section
      className="card knowledge-card"
      aria-labelledby="knowledge-preview"
    >
      <h2 id="knowledge-preview">Retrieval preview</h2>
      <form
        className="knowledge-form"
        onSubmit={async (event) => {
          event.preventDefault();
          const query = String(
            new FormData(event.currentTarget).get("query"),
          ).trim();
          setData(undefined);
          if (query)
            setData(
              await mutation.run(() =>
                api.retrievalPreview({ query, method, top_k: 5 }),
              ),
            );
        }}
      >
        <label className="knowledge-field">
          Evidence query
          <textarea name="query" required maxLength={2000} rows={3} />
        </label>
        <MethodSelect value={method} onChange={setMethod} />
        <Button disabled={mutation.busy}>
          {mutation.busy ? "Searching…" : "Search evidence"}
        </Button>
      </form>
      {mutation.error && (
        <Notice title="Preview unavailable" kind="error">
          {mutation.error.message}
        </Notice>
      )}
      {data && <EvidenceList data={data} />}
    </section>
  );
}

export function KnowledgeListScreen() {
  const { api } = useAuth();
  const [filters, setFilters] = useState({
    q: "",
    status: "",
    source_type: "",
    page: 1,
  });
  const load = useCallback(
    (signal: AbortSignal) => api.knowledgeList(filters, signal),
    [api, filters],
  );
  const result = useResource(JSON.stringify(filters), load);
  return (
    <>
      <PageHeading
        eyebrow="Agent workspace"
        title="Knowledge base"
        action={
          <ButtonLink href="/agent/knowledge/new">New article</ButtonLink>
        }
      >
        Manage the sources agents use to research support requests.
      </PageHeading>
      <div className="knowledge-grid">
        <section className="card knowledge-card" aria-label="Articles">
          <form
            className="knowledge-filters"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              setFilters({
                q: String(form.get("q")).trim(),
                status: String(form.get("status")),
                source_type: String(form.get("source_type")),
                page: 1,
              });
            }}
          >
            <label className="knowledge-field">
              Search articles <input name="q" type="search" maxLength={200} />
            </label>
            <label className="knowledge-field">
              Status
              <select name="status">
                <option value="">All statuses</option>
                <option value="DRAFT">Draft</option>
                <option value="PUBLISHED">Published</option>
                <option value="ARCHIVED">Archived</option>
              </select>
            </label>
            <label className="knowledge-field">
              Source type
              <select name="source_type">
                <option value="">All sources</option>
                <SourceChoices />
              </select>
            </label>
            <Button>Apply filters</Button>
          </form>
          {result.loading && <Loading label="Loading articles…" />}
          {result.error && (
            <Notice
              title="Could not load articles"
              kind="error"
              action={
                <Button onClick={result.reload} variant="secondary">
                  Try again
                </Button>
              }
            >
              {result.error.message}
            </Notice>
          )}
          {result.data &&
            !result.loading &&
            !result.error &&
            (result.data.items.length === 0 ? (
              <Empty title="No matching articles">
                Try different filters or create a draft.
              </Empty>
            ) : (
              <>
                <ul className="knowledge-results">
                  {result.data.items.map((article) => (
                    <li key={article.id}>
                      <Link
                        className="text-link"
                        href={`/agent/knowledge/${article.id}`}
                      >
                        {article.title}
                      </Link>
                      <p className="field-help">
                        {article.status} · Revision {article.revision} ·{" "}
                        {sources[article.source_type]}
                      </p>
                      <p className="field-help">
                        {article.source_name} · Updated{" "}
                        {date(article.updated_at)}
                      </p>
                    </li>
                  ))}
                </ul>
                <div className="knowledge-actions" aria-label="Article pages">
                  <Button
                    variant="secondary"
                    disabled={filters.page <= 1}
                    onClick={() =>
                      setFilters({ ...filters, page: filters.page - 1 })
                    }
                  >
                    Previous
                  </Button>
                  <span>
                    Page {filters.page} · {result.data.total} articles
                  </span>
                  <Button
                    variant="secondary"
                    disabled={
                      filters.page * result.data.page_size >= result.data.total
                    }
                    onClick={() =>
                      setFilters({ ...filters, page: filters.page + 1 })
                    }
                  >
                    Next
                  </Button>
                </div>
              </>
            ))}
          {result.data &&
            !result.loading &&
            !result.error &&
            !result.data.items.length &&
            filters.page > 1 && (
              <Button
                variant="secondary"
                onClick={() =>
                  setFilters({ ...filters, page: filters.page - 1 })
                }
              >
                Previous page
              </Button>
            )}
        </section>
        <Preview />
      </div>
    </>
  );
}

function ArticleForm({
  article,
  onSave,
  busy,
}: {
  article?: Schemas["ArticleDetail"];
  onSave: (data: Schemas["ArticleInput"]) => void;
  busy: boolean;
}) {
  const [source, setSource] = useState<Schemas["SourceType"]>(
    article?.source_type ?? "INTERNAL_PROCEDURE",
  );
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const value = (name: string) => String(fields.get(name) ?? "").trim();
    onSave({
      title: value("title"),
      body: value("body"),
      source_name: value("source_name"),
      source_type: source,
      source_url:
        source === "SYNTHETIC_TEST" ? null : value("source_url") || null,
      rights_reference: value("rights_reference"),
    });
  }
  return (
    <form className="knowledge-form" onSubmit={submit}>
      <label className="knowledge-field">
        Title{" "}
        <input
          name="title"
          required
          maxLength={200}
          defaultValue={article?.title}
        />
      </label>
      <label className="knowledge-field">
        Article body
        <textarea
          name="body"
          required
          maxLength={100000}
          rows={10}
          defaultValue={article?.body}
        />
      </label>
      <label className="knowledge-field">
        Source type
        <select
          name="source_type"
          value={source}
          disabled={article?.source_type === "SYNTHETIC_TEST"}
          onChange={(event) =>
            setSource(event.target.value as Schemas["SourceType"])
          }
        >
          {article && article.source_type !== "SYNTHETIC_TEST" ? (
            <>
              <option value="INTERNAL_PROCEDURE">Internal procedure</option>
              <option value="APPROVED_REFERENCE">Approved reference</option>
            </>
          ) : (
            <SourceChoices />
          )}
        </select>
      </label>
      <label className="knowledge-field">
        Source name{" "}
        <input
          name="source_name"
          required
          maxLength={300}
          defaultValue={article?.source_name}
        />
      </label>
      {source !== "SYNTHETIC_TEST" && (
        <label className="knowledge-field">
          Source URL
          <input
            name="source_url"
            type="url"
            maxLength={2048}
            required={source === "APPROVED_REFERENCE"}
            defaultValue={article?.source_url ?? ""}
          />
        </label>
      )}
      <label className="knowledge-field">
        Reuse approval or license reference
        <input
          name="rights_reference"
          required
          maxLength={500}
          defaultValue={article?.rights_reference}
        />
      </label>
      <Button disabled={busy}>{busy ? "Saving…" : "Save draft"}</Button>
    </form>
  );
}

export function NewArticleScreen() {
  const { api, announce } = useAuth();
  const router = useRouter();
  const mutation = useMutation();
  return (
    <>
      <PageHeading eyebrow="Knowledge base" title="New article">
        Use sources approved for reuse. Drafts are visible to agents.
      </PageHeading>
      <section
        className="card knowledge-card knowledge-editor"
        aria-label="Article editor"
      >
        {mutation.error && (
          <Notice kind="error" title="Draft could not be saved">
            {mutation.error.message}
          </Notice>
        )}
        <ArticleForm
          busy={mutation.busy}
          onSave={async (data) => {
            const article = await mutation.run(() => api.createArticle(data));
            if (article) {
              announce("Draft saved.");
              router.push(`/agent/knowledge/${article.id}`);
            }
          }}
        />
      </section>
    </>
  );
}

function History({ id }: { id: string }) {
  const { api } = useAuth();
  const [page, setPage] = useState(1);
  const load = useCallback(
    (signal: AbortSignal) => api.articleRevisions(id, page, signal),
    [api, id, page],
  );
  const history = useResource(`${id}:revisions:${page}`, load);
  return (
    <>
      {history.loading && <Loading label="Loading revisions…" />}
      {history.error && (
        <Notice kind="error" title="Could not load revisions">
          {history.error.message}
        </Notice>
      )}
      {history.data && !history.loading && !history.error && (
        <>
          <ul className="knowledge-results">
            {history.data.items.map((item) => (
              <li key={item.revision_id}>
                <Link
                  className="text-link"
                  href={`/agent/knowledge/${id}?revision=${item.revision}`}
                >
                  Revision {item.revision} · {item.title}
                </Link>
                <p className="field-help">
                  {item.source_name} · {date(item.created_at)}
                </p>
              </li>
            ))}
          </ul>
          {(page > 1 || page * history.data.page_size < history.data.total) && (
            <div className="knowledge-actions">
              <Button
                variant="secondary"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                Previous revisions
              </Button>
              <Button
                variant="secondary"
                disabled={page * history.data.page_size >= history.data.total}
                onClick={() => setPage(page + 1)}
              >
                Next revisions
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}

export function ArticleScreen({
  id,
  revision,
}: {
  id: string;
  revision?: number;
}) {
  const { api, announce } = useAuth();
  const [editing, setEditing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const mutation = useMutation();
  const load = useCallback(
    (signal: AbortSignal) => api.article(id, revision, signal),
    [api, id, revision],
  );
  const result = useResource(`article:${id}:${revision ?? "current"}`, load);
  const article = result.data;
  const indexing =
    !revision && ["PENDING", "RUNNING"].includes(article?.index_state ?? "");
  useEffect(() => {
    if (indexing && !result.loading && !result.error) {
      const timer = window.setTimeout(result.reload, 5000);
      return () => window.clearTimeout(timer);
    }
  }, [indexing, result.loading, result.error, result.reload]);
  async function lifecycle(operation: Schemas["Operation"], message: string) {
    if (!article || result.loading || result.error) return false;
    const saved = await mutation.run(() =>
      api.articleLifecycle(id, {
        expected_generation: article.generation,
        operation,
      }),
    );
    if (!saved) return false;
    announce(message);
    result.reload();
    return true;
  }
  const disabled = result.loading || mutation.busy || !!result.error;
  return (
    <>
      <PageHeading
        eyebrow="Knowledge base"
        title={article?.title ?? "Knowledge article"}
        action={
          <ButtonLink href="/agent/knowledge" secondary>
            All articles
          </ButtonLink>
        }
      />
      {result.loading && !article && <Loading label="Loading article…" />}
      {result.error && (
        <Notice
          title="Could not load article"
          kind="error"
          action={
            <Button onClick={result.reload} variant="secondary">
              Refresh article
            </Button>
          }
        >
          {result.error.message}
        </Notice>
      )}
      {article && !result.error && (
        <div className="knowledge-grid">
          <section className="card knowledge-card" aria-label="Article content">
            <p className="knowledge-status" role="status">
              {revision ? "Revision history" : article.status} · Revision{" "}
              {article.revision} · {indexLabels[article.index_state]}
            </p>
            {mutation.error && (
              <Notice
                title="Article change could not be confirmed"
                kind="error"
                action={
                  <Button variant="secondary" onClick={result.reload}>
                    Refresh article
                  </Button>
                }
              >
                {mutation.error.message}
              </Notice>
            )}
            {editing ? (
              <>
                {article.status === "PUBLISHED" && (
                  <Notice title="Editing creates a new draft">
                    Saving will remove the current revision from retrieval until
                    the new draft is published.
                  </Notice>
                )}
                <ArticleForm
                  article={article}
                  key={article.generation}
                  busy={disabled}
                  onSave={async (data) => {
                    const saved = await mutation.run(() =>
                      api.editArticle(id, {
                        ...data,
                        expected_generation: article.generation,
                      }),
                    );
                    if (saved) {
                      setEditing(false);
                      setHistoryOpen(false);
                      announce("New revision saved as a draft.");
                      result.reload();
                    }
                  }}
                />
                <Button
                  variant="quiet"
                  disabled={mutation.busy}
                  onClick={() => setEditing(false)}
                >
                  Cancel edit
                </Button>
              </>
            ) : (
              <>
                <p className="knowledge-body">{article.body}</p>
                {revision ? (
                  <ButtonLink href={`/agent/knowledge/${id}`} secondary>
                    Current article
                  </ButtonLink>
                ) : (
                  article.status !== "ARCHIVED" && (
                    <div className="knowledge-actions">
                      <Button
                        variant="secondary"
                        disabled={disabled}
                        onClick={() => setEditing(true)}
                      >
                        Edit article
                      </Button>
                      {article.status === "DRAFT" ? (
                        <Button
                          disabled={disabled}
                          onClick={() =>
                            lifecycle(
                              "PUBLISH",
                              "Article published. Indexing queued.",
                            )
                          }
                        >
                          Publish article
                        </Button>
                      ) : (
                        <>
                          <Button
                            variant="secondary"
                            disabled={disabled}
                            onClick={() =>
                              lifecycle("UNPUBLISH", "Article unpublished.")
                            }
                          >
                            Unpublish article
                          </Button>
                          <Button
                            variant="secondary"
                            disabled={disabled || indexing}
                            onClick={() =>
                              lifecycle("REINDEX", "Reindexing queued.")
                            }
                          >
                            Reindex article
                          </Button>
                        </>
                      )}
                      <ConfirmAction
                        label="Archive article"
                        title="Archive this article?"
                        disabled={disabled}
                        onConfirm={() =>
                          lifecycle("ARCHIVE", "Article archived.")
                        }
                      >
                        The article will leave retrieval and remain available in
                        history.
                      </ConfirmAction>
                    </div>
                  )
                )}
              </>
            )}
          </section>
          <aside
            className="card knowledge-card"
            aria-label="Article provenance"
          >
            <h2>Source and revisions</h2>
            <dl className="knowledge-provenance">
              <dt>Source</dt>
              <dd>
                {article.source_name} · {sources[article.source_type]}
              </dd>
              <dt>Reuse approval</dt>
              <dd>{article.rights_reference}</dd>
              <dt>Revision saved</dt>
              <dd>{date(article.revision_created_at)}</dd>
              <dt>Editor ID</dt>
              <dd>{article.editor_id}</dd>
            </dl>
            <SourceLink url={article.source_url} />
            <Button
              variant="quiet"
              onClick={() => setHistoryOpen(!historyOpen)}
            >
              {historyOpen ? "Hide revisions" : "Show revisions"}
            </Button>
            {historyOpen && <History id={id} />}
          </aside>
        </div>
      )}
    </>
  );
}
