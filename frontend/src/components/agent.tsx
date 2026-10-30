"use client";
import Link from "next/link";
import { useCallback, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  RefreshCw,
  Inbox,
  Flag,
  UserRound,
  Clock3,
  CheckCheck,
  Play,
  RotateCcw,
  Search,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import { ApiError, queryString } from "@/lib/api/client";
import type { Agent, AgentEvent, Priority, Status } from "@/lib/api/types";
import {
  collectPages,
  date,
  isUuid,
  pageIndex,
  parseInbox,
  priorities,
  shortId,
  statuses,
  title,
  transitions,
} from "@/lib/tickets";
import {
  Button,
  ButtonLink,
  ConfirmAction,
  Empty,
  Loading,
  PageHeading,
  TextLink,
} from "./ui";
import {
  Conversation,
  ErrorNotice,
  OriginalRequest,
  Pagination,
  PriorityBadge,
  StatusBadge,
  TicketList,
} from "./ticket-ui";
import { Composer } from "./composer";

export function AgentDashboard() {
  const { api } = useAuth();
  const load = useCallback(
    (signal: AbortSignal) =>
      collectPages((page, s) => api.inbox({ page, page_size: 100 }, s), signal),
    [api],
  );
  const resource = useResource("agent-overview", load);
  const data = resource.data;
  return (
    <>
      <PageHeading
        eyebrow="THE SUPPORT DESK"
        title="A clear view of the queue."
        action={
          <ButtonLink href="/agent/tickets">
            Open inbox <Inbox size={16} aria-hidden="true" />
          </ButtonLink>
        }
      >
        See what needs attention and pick up the next conversation.
      </PageHeading>
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}
      <div className="section-toolbar">
        <span className="field-help">
          Complete queue counts across all pages. Refresh for changes.
        </span>
        <Button
          variant="quiet"
          onClick={resource.reload}
          disabled={resource.loading}
        >
          <RefreshCw size={14} aria-hidden="true" />
          Refresh
        </Button>
      </div>
      {!data ? (
        resource.loading && <Loading label="Loading the complete queue…" />
      ) : (
        <>
          <div className="stat-grid agent-stats">
            {[
              {
                label: "Unassigned",
                count: data.filter((t) => !t.assigned_agent_id).length,
                icon: UserRound,
                query: "unassigned_only=true",
                note: "Across all statuses",
              },
              {
                label: "Open",
                count: data.filter((t) => t.status === "OPEN").length,
                icon: Inbox,
                query: "status=OPEN",
                note: "Ready for a first look",
              },
              {
                label: "In progress",
                count: data.filter((t) => t.status === "IN_PROGRESS").length,
                icon: Clock3,
                query: "status=IN_PROGRESS",
                note: "Conversations in motion",
              },
              {
                label: "High priority",
                count: data.filter((t) => t.priority === "HIGH").length,
                icon: Flag,
                query: "priority=HIGH",
                note: "Across all statuses",
              },
            ].map(({ label, count, icon: Icon, query, note }) => (
              <Link
                className="stat-card stat-link"
                href={`/agent/tickets?${query}`}
                key={label}
                aria-label={`View ${label.toLowerCase()} tickets: ${count}`}
              >
                <div>
                  <span>{label}</span>
                  <Icon size={18} strokeWidth={1.6} aria-hidden="true" />
                </div>
                <strong>{count.toLocaleString()}</strong>
                <p>{note}</p>
              </Link>
            ))}
          </div>
          <section className="card">
            <div className="card-heading">
              <div>
                <h2>Recently updated</h2>
                <p>
                  Latest updates across {data.length.toLocaleString()}{" "}
                  authorized requests.
                </p>
              </div>
              <TextLink href="/agent/tickets">View inbox</TextLink>
            </div>
            {data.length ? (
              <TicketList
                agent
                items={[...data]
                  .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
                  .slice(0, 7)}
              />
            ) : (
              <Empty title="A quiet queue.">
                Customer requests will appear here when they arrive.
              </Empty>
            )}
          </section>
        </>
      )}
    </>
  );
}

export function AgentInbox() {
  const { api, profile } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const serialized = params.toString();
  const query = useMemo(
    () => parseInbox(new URLSearchParams(serialized)),
    [serialized],
  );
  const load = useCallback(
    (signal: AbortSignal) => api.inbox(query, signal),
    [api, query],
  );
  const resource = useResource(`inbox:${queryString(query)}`, load);
  const loadAgents = useCallback(
    (signal: AbortSignal) =>
      collectPages((page, s) => api.agents(page, s), signal),
    [api],
  );
  const directory = useResource("inbox-agents", loadAgents);
  const agents = directory.data || [];
  const current = query.unassigned_only
    ? "unassigned"
    : query.assigned_agent_id || "";
  const href = (page: number) =>
    "/agent/tickets" + queryString({ ...query, page });
  const filtered = !!(
    query.status ||
    query.priority ||
    query.search ||
    query.assigned_agent_id ||
    query.unassigned_only
  );
  return (
    <>
      <PageHeading
        eyebrow="EVERY REQUEST, IN CONTEXT"
        title="Ticket inbox"
        action={
          <Button
            variant="secondary"
            disabled={resource.loading}
            onClick={() => {
              resource.reload();
              directory.reload();
            }}
          >
            <RefreshCw size={15} aria-hidden="true" />
            Refresh inbox
          </Button>
        }
      >
        Find the right conversation. Keep the next step clear.
      </PageHeading>
      <form
        key={serialized}
        className="card inbox-filters"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const next = new URLSearchParams();
          const search = String(form.get("search") || "").trim();
          const assignment = String(form.get("assignment") || "");
          if (search) next.set("search", search);
          for (const name of ["status", "priority", "order"]) {
            const value = String(form.get(name) || "");
            if (value) next.set(name, value);
          }
          if (assignment === "unassigned") next.set("unassigned_only", "true");
          else if (assignment) next.set("assigned_agent_id", assignment);
          next.set("page", "1");
          router.push("/agent/tickets?" + next.toString());
        }}
      >
        <div className="field filter-search">
          <label htmlFor="inbox-search">Subject or full ticket ID</label>
          <div className="search-input">
            <Search size={16} aria-hidden="true" />
            <input
              id="inbox-search"
              name="search"
              maxLength={200}
              defaultValue={query.search || ""}
              placeholder="Search requests…"
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="status-filter">Status</label>
          <select
            id="status-filter"
            name="status"
            defaultValue={query.status || ""}
          >
            <option value="">All statuses</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {title(s)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="priority-filter">Priority</label>
          <select
            id="priority-filter"
            name="priority"
            defaultValue={query.priority || ""}
          >
            <option value="">All priorities</option>
            {priorities.map((s) => (
              <option key={s} value={s}>
                {title(s)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="assignment-filter">Assignment</label>
          <select
            id="assignment-filter"
            name="assignment"
            defaultValue={current}
          >
            <option value="">Any assignment</option>
            <option value="unassigned">Unassigned only</option>
            {profile && <option value={profile.id}>Assigned to me</option>}
            {agents
              .filter((a) => a.id !== profile?.id)
              .map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agentName(agent)}
                </option>
              ))}
            {query.assigned_agent_id &&
              query.assigned_agent_id !== profile?.id &&
              !agents.some((a) => a.id === query.assigned_agent_id) && (
                <option value={query.assigned_agent_id}>
                  Agent #{shortId(query.assigned_agent_id)}
                </option>
              )}
          </select>
        </div>
        <div className="field">
          <label htmlFor="order-filter">Sort by creation</label>
          <select id="order-filter" name="order" defaultValue={query.order}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </div>
        <div className="filter-actions">
          <Button type="submit" disabled={resource.loading}>
            Apply filters
          </Button>
          <Link className="text-link" href="/agent/tickets">
            Reset
          </Link>
        </div>
      </form>
      {directory.error && (
        <ErrorNotice error={directory.error} retry={directory.reload} />
      )}{" "}
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}
      <section className="card">
        <div className="card-heading">
          <div>
            <h2>{filtered ? "Filtered queue" : "All requests"}</h2>
            <p>
              {resource.data
                ? `${resource.data.total.toLocaleString()} matching requests`
                : "Loading requests…"}
            </p>
          </div>
          <span className="queue-order">
            {query.order === "oldest" ? "Oldest" : "Newest"} created first
          </span>
        </div>
        {!resource.data ? (
          resource.loading && <Loading />
        ) : (
          <>
            {resource.data.items.length ? (
              <TicketList
                agent
                items={resource.data.items}
                inboxQuery={queryString(query).slice(1)}
              />
            ) : (
              <Empty
                title={
                  filtered
                    ? "No requests match these filters."
                    : "No requests on this page."
                }
                action={
                  <ButtonLink secondary href="/agent/tickets">
                    Reset the inbox
                  </ButtonLink>
                }
              >
                {filtered
                  ? "Try a broader search or a different assignment."
                  : "Customer requests appear here after submission."}
              </Empty>
            )}
            <Pagination
              page={query.page || 1}
              total={resource.data.total}
              href={href}
            />
          </>
        )}
      </section>
    </>
  );
}
function agentName(agent: Agent) {
  return agent.display_name || `Agent #${shortId(agent.id)}`;
}
function eventChange(event: AgentEvent) {
  const after = event.metadata.after;
  if (!after || typeof after !== "object" || Array.isArray(after)) return "";
  const fields = after as Record<string, unknown>;
  if (
    typeof fields.status === "string" &&
    statuses.includes(fields.status as Status)
  )
    return title(fields.status);
  if (
    typeof fields.priority === "string" &&
    priorities.includes(fields.priority as Priority)
  )
    return `${title(fields.priority)} priority`;
  if ("assigned_agent_id" in fields)
    return fields.assigned_agent_id === null
      ? "Unassigned"
      : typeof fields.assigned_agent_id === "string" &&
          isUuid(fields.assigned_agent_id)
        ? `Assigned to agent #${shortId(fields.assigned_agent_id)}`
        : "";
  return "";
}

export function AgentTicketScreen({ id }: { id: string }) {
  const { api, profile, announce } = useAuth();
  const params = useSearchParams();
  const messagesPage = pageIndex(params.get("messages_page"));
  const eventsPage = pageIndex(params.get("events_page"));
  const load = useCallback(
    (signal: AbortSignal) =>
      isUuid(id)
        ? api.agentDetail(
            id,
            {
              messages_page: messagesPage,
              events_page: eventsPage,
              page_size: 25,
            },
            signal,
          )
        : Promise.reject(new ApiError(404, "ticket_not_found")),
    [api, id, messagesPage, eventsPage],
  );
  const resource = useResource(
    `agent-ticket:${id}:${messagesPage}:${eventsPage}`,
    load,
    `agent-ticket:${id}`,
  );
  const loadAgents = useCallback(
    (signal: AbortSignal) =>
      collectPages((page, s) => api.agents(page, s), signal),
    [api],
  );
  const directory = useResource("workspace-agents", loadAgents);
  const mutation = useMutation();
  const data = resource.data;
  const blocked =
    mutation.busy ||
    resource.loading ||
    !!resource.error ||
    mutation.error?.status === 409;
  const agents = directory.data || [];
  const back =
    "/agent/tickets" +
    (params.get("inbox")
      ? "?" + new URLSearchParams(params.get("inbox")!).toString()
      : "");
  const href = (key: string, page: number) => {
    const next = new URLSearchParams(params.toString());
    next.set(key, String(page));
    return `/agent/tickets/${id}?${next}`;
  };
  async function change(action: () => Promise<unknown>, message: string) {
    const result = await mutation.run(action);
    if (result) {
      announce(message);
      resource.reload();
      return true;
    }
    return false;
  }
  if (!isUuid(id))
    return (
      <Empty
        title="This ticket link is invalid."
        action={<ButtonLink href="/agent/tickets">Back to inbox</ButtonLink>}
      >
        Choose a request from the inbox.
      </Empty>
    );
  return (
    <>
      <Link className="back-link ticket-back" href={back}>
        <ArrowLeft size={15} aria-hidden="true" />
        Back to inbox
      </Link>
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}{" "}
      {!data ? (
        resource.loading && <Loading label="Opening the ticket workspace…" />
      ) : (
        <>
          <PageHeading
            eyebrow={`TICKET #${shortId(id)}`}
            title={data.subject}
            action={
              <Button
                variant="secondary"
                disabled={resource.loading}
                onClick={() => {
                  resource.reload();
                  directory.reload();
                }}
              >
                <RefreshCw size={14} aria-hidden="true" />
                {resource.loading ? "Refreshing…" : "Refresh"}
              </Button>
            }
          >
            Created {date(data.created_at)} · Updated {date(data.updated_at)}
          </PageHeading>
          <div className="workspace-ticket-summary">
            <div className="ticket-badges">
              <StatusBadge status={data.status} />
              <PriorityBadge priority={data.priority} />
            </div>
            <span>
              {data.assigned_agent_id === profile?.id
                ? "Assigned to you"
                : data.assigned_agent_id
                  ? `Assigned to ${agents.find((a) => a.id === data.assigned_agent_id)?.display_name || "agent #" + shortId(data.assigned_agent_id)}`
                  : "Unassigned request"}
            </span>
            <a className="text-link" href="#ticket-controls">
              Manage request
            </a>
          </div>
          {mutation.error && (
            <ErrorNotice
              error={mutation.error}
              retry={() => {
                mutation.clearError();
                resource.reload();
                directory.reload();
              }}
            />
          )}
          <div className="agent-detail-layout">
            <div className="card conversation-card">
              <div className="card-heading">
                <div>
                  <h2>Conversation</h2>
                  <p>Public replies and clearly marked internal notes.</p>
                </div>
                <span className="field-help">
                  {data.messages.total} messages
                </span>
              </div>
              <OriginalRequest
                description={data.description}
                createdAt={data.created_at}
              />
              <Conversation agent messages={data.messages.items} />
              <Pagination
                label="Messages"
                page={data.messages.page}
                total={data.messages.total}
                href={(n) => href("messages_page", n)}
              />
              <Composer
                agent
                resolved={data.status === "RESOLVED"}
                disabled={blocked}
                onSend={async (body, visibility) =>
                  change(
                    () => api.agentReply(id, { body, visibility }),
                    visibility === "INTERNAL"
                      ? "Internal note saved. Only agents can see it."
                      : "Public reply sent to the conversation.",
                  )
                }
              />
            </div>
            <aside className="ticket-info-rail">
              <section
                className="card ticket-controls"
                id="ticket-controls"
                tabIndex={-1}
              >
                <h2>Manage request</h2>
                <div className="control-section">
                  <h3>Assignment</h3>
                  {!data.assigned_agent_id && (
                    <Button
                      disabled={blocked}
                      onClick={() =>
                        void change(
                          () => api.claim(id),
                          "Ticket claimed. You are now assigned.",
                        )
                      }
                    >
                      <UserRound size={15} aria-hidden="true" />
                      Claim ticket
                    </Button>
                  )}
                  {directory.error ? (
                    <ErrorNotice
                      error={directory.error}
                      retry={directory.reload}
                    />
                  ) : (
                    <div className="field">
                      <label htmlFor="ticket-assignment">Assigned agent</label>
                      <select
                        id="ticket-assignment"
                        value={data.assigned_agent_id || ""}
                        disabled={
                          blocked || directory.loading || !directory.data
                        }
                        onChange={(event) => {
                          const value = event.target.value;
                          if (value)
                            void change(
                              () =>
                                api.assignment(id, {
                                  assigned_agent_id: value,
                                }),
                              "Assignment updated.",
                            );
                        }}
                      >
                        <option value="" disabled>
                          Unassigned
                        </option>
                        {agents.map((agent) => (
                          <option value={agent.id} key={agent.id}>
                            {agentName(agent)}
                            {agent.id === profile?.id ? " (you)" : ""}
                          </option>
                        ))}
                        {data.assigned_agent_id &&
                          !agents.some(
                            (a) => a.id === data.assigned_agent_id,
                          ) && (
                            <option value={data.assigned_agent_id}>
                              Agent #{shortId(data.assigned_agent_id)}
                            </option>
                          )}
                      </select>
                    </div>
                  )}
                  {data.assigned_agent_id && (
                    <ConfirmAction
                      label="Unassign ticket"
                      title="Return this request to the queue?"
                      disabled={blocked}
                      onConfirm={() =>
                        change(
                          () => api.assignment(id, { assigned_agent_id: null }),
                          "Ticket returned to the unassigned queue.",
                        )
                      }
                    >
                      Another agent will be able to claim this request.
                    </ConfirmAction>
                  )}
                </div>
                <div className="control-section">
                  <div className="control-heading">
                    <h3>Status</h3>
                    <StatusBadge status={data.status} />
                  </div>
                  <div className="status-actions">
                    {transitions[data.status].includes("IN_PROGRESS") && (
                      <Button
                        variant="secondary"
                        disabled={blocked}
                        onClick={() =>
                          void change(
                            () => api.status(id, { status: "IN_PROGRESS" }),
                            "Ticket marked in progress.",
                          )
                        }
                      >
                        <Play size={14} aria-hidden="true" />
                        Start work
                      </Button>
                    )}
                    {data.status === "RESOLVED" ? (
                      <Button
                        disabled={blocked}
                        onClick={() =>
                          void change(
                            () => api.status(id, { status: "REOPENED" }),
                            "Ticket reopened. Public replies are available again.",
                          )
                        }
                      >
                        <RotateCcw size={14} aria-hidden="true" />
                        Reopen ticket
                      </Button>
                    ) : (
                      <ConfirmAction
                        label="Resolve ticket"
                        title="Is this request ready to resolve?"
                        disabled={blocked}
                        onConfirm={() =>
                          change(
                            () => api.status(id, { status: "RESOLVED" }),
                            "Ticket resolved. Public replies are closed until it is reopened.",
                          )
                        }
                      >
                        Public replies will close. The customer can reopen the
                        conversation if the issue returns.
                      </ConfirmAction>
                    )}
                  </div>
                  {data.resolved_at && (
                    <p className="field-help">
                      Resolved {date(data.resolved_at)}
                    </p>
                  )}
                </div>
                <div className="control-section">
                  <div className="field">
                    <label htmlFor="ticket-priority">Priority</label>
                    <select
                      id="ticket-priority"
                      value={data.priority}
                      disabled={blocked}
                      onChange={(event) => {
                        const priority = event.target.value as Priority;
                        void change(
                          () => api.priority(id, { priority }),
                          "Priority updated.",
                        );
                      }}
                    >
                      {priorities.map((priority) => (
                        <option value={priority} key={priority}>
                          {title(priority)}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </section>
              <section className="card ticket-info">
                <h2>Customer reference</h2>
                <p className="mono">{data.customer_id}</p>
                <p className="field-help mt-3">
                  Ask for useful context in the public conversation. Never
                  request passwords or verification codes.
                </p>
              </section>
              <section className="card activity-card">
                <div className="activity-heading">
                  <h2>Activity timeline</h2>
                  <CheckCheck size={16} aria-hidden="true" />
                </div>
                {data.events.items.length ? (
                  <ol className="activity-list">
                    {data.events.items.map((event) => (
                      <li key={event.id}>
                        <strong>{title(event.action)}</strong>
                        {eventChange(event) && (
                          <span>{eventChange(event)}</span>
                        )}
                        <span>
                          {event.actor_id === profile?.id
                            ? "You"
                            : event.actor_id === data.customer_id
                              ? "Customer"
                              : event.actor_id
                                ? agents.find((a) => a.id === event.actor_id)
                                    ?.display_name ||
                                  `Participant #${shortId(event.actor_id)}`
                                : "System"}
                        </span>
                        <time dateTime={event.created_at}>
                          {date(event.created_at)}
                        </time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="field-help">No activity on this page.</p>
                )}
                <Pagination
                  label="Activity"
                  page={data.events.page}
                  total={data.events.total}
                  href={(n) => href("events_page", n)}
                />
              </section>
            </aside>
          </div>
        </>
      )}
    </>
  );
}
