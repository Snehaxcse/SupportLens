"use client";
import Link from "next/link";
import { useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ArrowUpRight,
  LockKeyhole,
} from "lucide-react";
import type {
  AgentMessage,
  AgentTicket,
  CustomerTicket,
  Priority,
  PublicMessage,
  Status,
} from "@/lib/api/types";
import type { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { date, shortId, title } from "@/lib/tickets";
import { Button, ButtonLink, Notice } from "./ui";

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`badge status-${status.toLowerCase()}`}>
      <span aria-hidden="true" />
      {title(status)}
    </span>
  );
}
export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`badge priority-${priority.toLowerCase()}`}>
      {priority === "HIGH" && <span aria-hidden="true">↑</span>}
      {title(priority)} priority
    </span>
  );
}
export function ErrorNotice({
  error,
  retry,
  retryLabel = "Refresh data",
}: {
  error: ApiError;
  retry: () => void;
  retryLabel?: string;
}) {
  const auth = useAuth();
  const [refreshError, setRefreshError] = useState("");
  return (
    <Notice
      kind="error"
      title={error.message}
      action={
        <div className="inline-actions">
          <Button variant="secondary" onClick={retry}>
            {retryLabel}
          </Button>
          {error.status === 403 && (
            <Button
              variant="secondary"
              onClick={() => {
                setRefreshError("");
                void auth
                  .refresh()
                  .catch(() =>
                    setRefreshError(
                      "Account refresh could not be completed. Check your connection or sign out and sign in again.",
                    ),
                  );
              }}
            >
              Refresh account
            </Button>
          )}
          {error.status === 404 && (
            <ButtonLink
              secondary
              href={
                auth.profile?.role === "AGENT"
                  ? "/agent/tickets"
                  : "/customer/tickets"
              }
            >
              Back to tickets
            </ButtonLink>
          )}
        </div>
      }
    >
      {error.requestId && (
        <span className="mono">Reference: {error.requestId}</span>
      )}
      {refreshError && (
        <p className="field-error" role="alert">
          {refreshError}
        </p>
      )}
    </Notice>
  );
}
export function Pagination({
  page,
  total,
  size = 25,
  href,
  label = "Tickets",
}: {
  page: number;
  total: number;
  size?: number;
  href: (n: number) => string;
  label?: string;
}) {
  const last = Math.max(1, Math.ceil(total / size));
  return (
    <nav className="pagination" aria-label={`${label} pagination`}>
      <p>
        Page <strong>{page}</strong> of {last}{" "}
        <span>
          · {total.toLocaleString()} {label.toLowerCase()}
        </span>
      </p>
      <div>
        {page > 1 ? (
          <Link
            className="pagination-link"
            href={href(page - 1)}
            aria-label={`Previous ${label.toLowerCase()} page`}
          >
            <ChevronLeft size={16} aria-hidden="true" />
            Previous
          </Link>
        ) : (
          <span className="pagination-link disabled" aria-disabled="true">
            <ChevronLeft size={16} aria-hidden="true" />
            Previous
          </span>
        )}
        {page < last ? (
          <Link
            className="pagination-link"
            href={href(page + 1)}
            aria-label={`Next ${label.toLowerCase()} page`}
          >
            Next
            <ChevronRight size={16} aria-hidden="true" />
          </Link>
        ) : (
          <span className="pagination-link disabled" aria-disabled="true">
            Next
            <ChevronRight size={16} aria-hidden="true" />
          </span>
        )}
        {last > 1 && page !== last && (
          <Link className="pagination-link" href={href(last)}>
            Last page
          </Link>
        )}
      </div>
    </nav>
  );
}
export function TicketList({
  items,
  agent = false,
  inboxQuery,
}: {
  items: CustomerTicket[] | AgentTicket[];
  agent?: boolean;
  inboxQuery?: string;
}) {
  const { profile } = useAuth();
  return (
    <div className={`ticket-list-wrap ${agent ? "agent-list" : ""}`}>
      <div className="ticket-list-head" aria-hidden="true">
        <span>REQUEST</span>
        <span>STATUS</span>
        <span>PRIORITY</span>
        {agent && <span>ASSIGNMENT</span>}
        <span>UPDATED</span>
      </div>
      <ul className="ticket-list">
        {items.map((ticket) => (
          <li className="ticket-row" key={ticket.id}>
            <div className="ticket-title-cell">
              <span className="ticket-id">#{shortId(ticket.id)}</span>
              <Link
                className="ticket-subject"
                href={`${agent ? "/agent" : "/customer"}/tickets/${ticket.id}${agent && inboxQuery ? "?inbox=" + encodeURIComponent(inboxQuery) : ""}`}
              >
                {ticket.subject}
                <ArrowUpRight size={14} aria-hidden="true" />
              </Link>
            </div>
            <div>
              <StatusBadge status={ticket.status} />
            </div>
            <div>
              <PriorityBadge priority={ticket.priority} />
            </div>
            {agent && "assigned_agent_id" in ticket && (
              <div className="assignment-cell">
                {ticket.assigned_agent_id === profile?.id
                  ? "You"
                  : ticket.assigned_agent_id
                    ? `Agent ${shortId(ticket.assigned_agent_id)}`
                    : "Unassigned"}
              </div>
            )}
            <time className="ticket-time" dateTime={ticket.updated_at}>
              {date(ticket.updated_at)}
            </time>
          </li>
        ))}
      </ul>
    </div>
  );
}
export function Conversation({
  messages,
  agent = false,
}: {
  messages: PublicMessage[] | AgentMessage[];
  agent?: boolean;
}) {
  if (!agent && messages.some((message) => message.visibility !== "PUBLIC"))
    return (
      <Notice
        kind="error"
        title="This conversation could not be displayed safely."
      />
    );
  return messages.length ? (
    <ol className="conversation" aria-label="Conversation messages">
      {messages.map((message) => {
        const internal = agent && message.visibility === "INTERNAL";
        return (
          <li
            className={`message ${internal ? "message-internal" : ""}`}
            key={message.id}
          >
            <div className="message-heading">
              <span
                className={`message-avatar ${message.author.kind === "AGENT" ? "agent-avatar" : ""}`}
              >
                {message.author.kind === "AGENT" ? "A" : "C"}
              </span>
              <div>
                <strong>
                  {message.author.display_name ||
                    (message.author.kind === "AGENT"
                      ? "Support agent"
                      : "Customer")}
                </strong>
                <span className="message-kind">
                  {title(message.author.kind)}
                  {internal ? " · Internal note" : " · Public reply"}
                </span>
              </div>
              <time dateTime={message.created_at}>
                {date(message.created_at)}
              </time>
            </div>
            {internal && (
              <div className="internal-label">
                <LockKeyhole size={12} aria-hidden="true" /> Only agents can see
                this note
              </div>
            )}
            <p className="plain-text">{message.body}</p>
          </li>
        );
      })}
    </ol>
  ) : (
    <div className="conversation-empty">
      <p>No replies yet.</p>
      <span>
        The original request is above. Continue the conversation below.
      </span>
    </div>
  );
}
export function OriginalRequest({
  description,
  createdAt,
}: {
  description: string;
  createdAt: string;
}) {
  return (
    <section className="original-request">
      <div>
        <h3>Original request</h3>
        <time dateTime={createdAt}>{date(createdAt)}</time>
      </div>
      <p className="plain-text">{description}</p>
    </section>
  );
}
