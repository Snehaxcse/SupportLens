"use client";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Plus,
  RefreshCw,
  ArrowLeft,
  Inbox,
  MessageSquareText,
  CheckCheck,
  RotateCcw,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import { ApiError } from "@/lib/api/client";
import {
  collectPages,
  date,
  isUuid,
  pageIndex,
  shortId,
  title,
  validateText,
} from "@/lib/tickets";
import {
  Button,
  ButtonLink,
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

export function CustomerDashboard() {
  const { api } = useAuth();
  const load = useCallback(
    (signal: AbortSignal) =>
      collectPages((n, s) => api.customerPage(n, s), signal),
    [api],
  );
  const resource = useResource("customer-overview", load);
  return (
    <>
      <PageHeading eyebrow="YOUR SUPPORT, IN FOCUS" title="Good to see you.">
        A clear view of your requests and what happens next.
      </PageHeading>
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}
      <div className="section-toolbar">
        <span className="field-help">
          Counts include every page of your authorized tickets.
        </span>
        <Button
          variant="quiet"
          disabled={resource.loading}
          onClick={resource.reload}
        >
          <RefreshCw size={14} aria-hidden="true" />
          Refresh
        </Button>
      </div>
      {!resource.data ? (
        resource.loading && <Loading label="Loading all your requests…" />
      ) : (
        <>
          <div className="stat-grid">
            {[
              {
                label: "All requests",
                count: resource.data.length,
                icon: Inbox,
                note: "Every conversation in one place",
              },
              {
                label: "Active requests",
                count: resource.data.filter((t) => t.status !== "RESOLVED")
                  .length,
                icon: MessageSquareText,
                note: "Open, in progress, or reopened",
              },
              {
                label: "Resolved",
                count: resource.data.filter((t) => t.status === "RESOLVED")
                  .length,
                icon: CheckCheck,
                note: "Reopen a request if you need to",
              },
            ].map(({ label, count, icon: Icon, note }) => (
              <article className="stat-card" key={label}>
                <div>
                  <span>{label}</span>
                  <Icon size={18} strokeWidth={1.6} aria-hidden="true" />
                </div>
                <strong>{count.toLocaleString()}</strong>
                <p>{note}</p>
              </article>
            ))}
          </div>
          <section className="card">
            <div className="card-heading">
              <div>
                <h2>Recent requests</h2>
                <p>Your most recently updated conversations.</p>
              </div>
              <TextLink href="/customer/tickets">View all</TextLink>
            </div>
            {resource.data.length ? (
              <TicketList
                items={[...resource.data]
                  .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
                  .slice(0, 5)}
              />
            ) : (
              <Empty
                title="Let’s start with your question."
                action={
                  <ButtonLink href="/customer/tickets/new">
                    <Plus size={16} aria-hidden="true" />
                    Create a request
                  </ButtonLink>
                }
              >
                Describe what’s happening. You can follow every update here.
              </Empty>
            )}
          </section>
          <section className="dashboard-note">
            <span className="empty-icon">
              <MessageSquareText
                size={24}
                strokeWidth={1.5}
                aria-hidden="true"
              />
            </span>
            <div>
              <h3>A little context goes a long way.</h3>
              <p>
                Include your device, the issue, and what you’ve tried. Keep
                passwords and payment details out of your request.
              </p>
            </div>
            <ButtonLink href="/customer/tickets/new" secondary>
              New request <Plus size={15} aria-hidden="true" />
            </ButtonLink>
          </section>
        </>
      )}
    </>
  );
}
export function CustomerTickets() {
  const { api } = useAuth();
  const params = useSearchParams();
  const page = pageIndex(params.get("page"));
  const load = useCallback(
    (signal: AbortSignal) => api.customerTickets(page, signal),
    [api, page],
  );
  const resource = useResource(`customer-list:${page}`, load);
  return (
    <>
      <PageHeading
        eyebrow="EVERY CONVERSATION"
        title="My tickets"
        action={
          <ButtonLink href="/customer/tickets/new">
            <Plus size={16} aria-hidden="true" />
            New request
          </ButtonLink>
        }
      >
        Your questions, their progress, and the next step.
      </PageHeading>
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}
      <section className="card">
        <div className="card-heading">
          <div>
            <h2>Your requests</h2>
            <p>Newest requests first.</p>
          </div>
          <Button
            variant="quiet"
            onClick={resource.reload}
            disabled={resource.loading}
          >
            <RefreshCw size={14} aria-hidden="true" />
            Refresh
          </Button>
        </div>
        {!resource.data ? (
          resource.loading && <Loading />
        ) : (
          <>
            {resource.data.items.length ? (
              <TicketList items={resource.data.items} />
            ) : (
              <Empty
                title={
                  resource.data.total
                    ? "No requests on this page."
                    : "No requests yet."
                }
                action={
                  <ButtonLink
                    href={
                      resource.data.total
                        ? "/customer/tickets"
                        : "/customer/tickets/new"
                    }
                  >
                    {resource.data.total
                      ? "Back to the first page"
                      : "Create a request"}
                  </ButtonLink>
                }
              >
                {resource.data.total
                  ? "Return to an earlier page to find your requests."
                  : "Start a conversation when you need a hand."}
              </Empty>
            )}
            <Pagination
              page={page}
              total={resource.data.total}
              href={(n) => `/customer/tickets?page=${n}`}
            />
          </>
        )}
      </section>
    </>
  );
}
export function NewTicket() {
  const { api, announce } = useAuth();
  const router = useRouter();
  const mutation = useMutation();
  const [errors, setErrors] = useState({ subject: "", description: "" });
  const subjectRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  return (
    <>
      <Link className="back-link ticket-back" href="/customer/tickets">
        <ArrowLeft size={15} aria-hidden="true" />
        All requests
      </Link>
      <PageHeading eyebrow="LET’S GET IT SORTED" title="What’s happening?">
        Give your request a clear subject and a little context. An agent can
        follow up in the conversation.
      </PageHeading>
      <div className="create-layout">
        <section className="card create-form-card">
          {mutation.error && (
            <ErrorNotice
              error={mutation.error}
              retry={() => router.push("/customer/tickets")}
              retryLabel="Check my tickets"
            />
          )}
          <form
            className="form-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const subject = String(data.get("subject") || "");
              const description = String(data.get("description") || "");
              const next = {
                subject: validateText(subject, 200),
                description: validateText(description, 20000),
              };
              setErrors(next);
              if (next.subject) {
                subjectRef.current?.focus();
                return;
              }
              if (next.description) {
                descriptionRef.current?.focus();
                return;
              }
              const result = await mutation.run(() =>
                api.createTicket({
                  subject: subject.trim(),
                  description: description.trim(),
                }),
              );
              if (result) {
                announce(
                  "Your request was created. You can follow the conversation here.",
                );
                router.push(`/customer/tickets/${result.id}`);
              }
            }}
          >
            <div className="field">
              <label htmlFor="subject">Subject</label>
              <input
                ref={subjectRef}
                id="subject"
                name="subject"
                placeholder="A short summary of the issue"
                required
                maxLength={200}
                disabled={mutation.busy}
                aria-invalid={!!errors.subject}
                aria-describedby={
                  errors.subject ? "subject-help subject-error" : "subject-help"
                }
              />
              <p id="subject-help" className="field-help">
                A clear headline helps agents understand your request. Maximum
                200 characters.
              </p>
              {errors.subject && (
                <p id="subject-error" className="field-error" role="alert">
                  {errors.subject}
                </p>
              )}
            </div>
            <div className="field">
              <label htmlFor="description">Tell us a little more</label>
              <textarea
                ref={descriptionRef}
                id="description"
                name="description"
                rows={9}
                placeholder="What happened, what did you expect, and what have you tried?"
                required
                maxLength={20000}
                disabled={mutation.busy}
                aria-invalid={!!errors.description}
                aria-describedby={
                  errors.description
                    ? "description-help description-error"
                    : "description-help"
                }
              />
              <p id="description-help" className="field-help">
                Include useful reproduction steps. Maximum 20,000 characters.
                Plain text only.
              </p>
              {errors.description && (
                <p id="description-error" className="field-error" role="alert">
                  {errors.description}
                </p>
              )}
            </div>
            <div className="form-footer">
              <ButtonLink href="/customer/tickets" secondary>
                Cancel
              </ButtonLink>
              <Button type="submit" disabled={mutation.busy}>
                {mutation.busy ? "Creating request…" : "Create request"}
              </Button>
            </div>
          </form>
        </section>
        <aside className="create-guidance">
          <h2>A helpful request includes</h2>
          <ol>
            <li>The device and app version you’re using</li>
            <li>What you were doing when the issue appeared</li>
            <li>Any steps you’ve already tried</li>
          </ol>
          <p>
            Never share passwords, verification codes, full card details, or
            other private account information.
          </p>
          <div className="guidance-divider" />
          <h3>What happens next?</h3>
          <p>
            Your request starts as Open. You can add replies, follow updates,
            and reopen it after resolution if the issue returns.
          </p>
        </aside>
      </div>
    </>
  );
}
export function CustomerTicketScreen({ id }: { id: string }) {
  const { api, announce } = useAuth();
  const params = useSearchParams();
  const messagesPage = pageIndex(params.get("messages_page"));
  const historyPage = pageIndex(params.get("history_page"));
  const valid = isUuid(id);
  const load = useCallback(
    (signal: AbortSignal) =>
      isUuid(id)
        ? api.customerDetail(
            id,
            {
              messages_page: messagesPage,
              history_page: historyPage,
              page_size: 25,
            },
            signal,
          )
        : Promise.reject(new ApiError(404, "ticket_not_found")),
    [api, id, messagesPage, historyPage],
  );
  const resource = useResource(
    `customer-ticket:${id}:${messagesPage}:${historyPage}`,
    load,
    `customer-ticket:${id}`,
  );
  const mutation = useMutation();
  const data = resource.data;
  const href = (key: string, page: number) => {
    const q = new URLSearchParams(params.toString());
    q.set(key, String(page));
    return `/customer/tickets/${id}?${q}`;
  };
  if (!valid)
    return (
      <Empty
        title="This request link is invalid."
        action={
          <ButtonLink href="/customer/tickets">Back to tickets</ButtonLink>
        }
      >
        Open one of your requests from the ticket list.
      </Empty>
    );
  return (
    <>
      <Link className="back-link ticket-back" href="/customer/tickets">
        <ArrowLeft size={15} aria-hidden="true" />
        All requests
      </Link>
      {resource.error && (
        <ErrorNotice error={resource.error} retry={resource.reload} />
      )}{" "}
      {!data ? (
        resource.loading && <Loading label="Opening your conversation…" />
      ) : (
        <>
          <PageHeading
            eyebrow={`REQUEST #${shortId(id)}`}
            title={data.subject}
            action={
              <div className="ticket-badges">
                <StatusBadge status={data.status} />
                <PriorityBadge priority={data.priority} />
              </div>
            }
          >
            Created {date(data.created_at)} · Updated {date(data.updated_at)}
          </PageHeading>
          {mutation.error && (
            <ErrorNotice
              error={mutation.error}
              retry={() => {
                mutation.clearError();
                resource.reload();
              }}
            />
          )}
          <div className="customer-detail-layout">
            <div className="card conversation-card">
              <div className="card-heading">
                <h2>Conversation</h2>
                <Button
                  variant="quiet"
                  disabled={resource.loading}
                  onClick={resource.reload}
                >
                  <RefreshCw size={14} aria-hidden="true" />
                  {resource.loading ? "Refreshing…" : "Refresh"}
                </Button>
              </div>
              <OriginalRequest
                description={data.description}
                createdAt={data.created_at}
              />
              <Conversation messages={data.messages.items} />
              <Pagination
                label="Messages"
                page={data.messages.page}
                total={data.messages.total}
                href={(n) => href("messages_page", n)}
              />
              <Composer
                resolved={data.status === "RESOLVED"}
                disabled={
                  mutation.busy ||
                  resource.loading ||
                  !!resource.error ||
                  mutation.error?.status === 409
                }
                onSend={async (body) => {
                  const result = await mutation.run(() =>
                    api.customerReply(id, { body }),
                  );
                  if (result) {
                    announce("Your reply was sent.");
                    resource.reload();
                    return true;
                  }
                  return false;
                }}
              />
            </div>
            <aside className="ticket-info-rail">
              <section className="card ticket-info">
                <h2>Request details</h2>
                <dl>
                  <div>
                    <dt>Status</dt>
                    <dd>
                      <StatusBadge status={data.status} />
                    </dd>
                  </div>
                  <div>
                    <dt>Priority</dt>
                    <dd>
                      <PriorityBadge priority={data.priority} />
                    </dd>
                  </div>
                  <div>
                    <dt>Last updated</dt>
                    <dd>{date(data.updated_at)}</dd>
                  </div>
                  {data.resolved_at && (
                    <div>
                      <dt>Resolved</dt>
                      <dd>{date(data.resolved_at)}</dd>
                    </div>
                  )}
                </dl>
                {data.status === "RESOLVED" ? (
                  <>
                    <p className="field-help">
                      Need more help with this issue? Reopen this conversation
                      before sending a reply.
                    </p>
                    <Button
                      disabled={
                        mutation.busy ||
                        resource.loading ||
                        !!resource.error ||
                        mutation.error?.status === 409
                      }
                      onClick={async () => {
                        const result = await mutation.run(() => api.reopen(id));
                        if (result) {
                          announce(
                            "Your request was reopened. You can reply again.",
                          );
                          resource.reload();
                        }
                      }}
                    >
                      <RotateCcw size={14} aria-hidden="true" />
                      Reopen request
                    </Button>
                  </>
                ) : (
                  <p className="field-help">
                    Your request is {title(data.status).toLowerCase()}. Add any
                    helpful updates in a public reply.
                  </p>
                )}
              </section>
              <section className="card activity-card">
                <h2>Ticket activity</h2>
                {data.history.items.length ? (
                  <ol className="activity-list">
                    {data.history.items.map((event) => (
                      <li key={event.id}>
                        <strong>{title(event.action)}</strong>
                        {event.after_status && (
                          <span>
                            {event.before_status
                              ? `${title(event.before_status)} → `
                              : ""}
                            {title(event.after_status)}
                          </span>
                        )}
                        {event.after_priority && (
                          <span>{title(event.after_priority)} priority</span>
                        )}
                        <time dateTime={event.created_at}>
                          {date(event.created_at)}
                        </time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="field-help">No public activity on this page.</p>
                )}
                <Pagination
                  label="Activity"
                  page={data.history.page}
                  total={data.history.total}
                  href={(n) => href("history_page", n)}
                />
              </section>
            </aside>
          </div>
        </>
      )}
    </>
  );
}
