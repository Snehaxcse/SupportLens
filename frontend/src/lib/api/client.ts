import type {
  Agent,
  AgentDetail,
  AgentMessage,
  AgentTicket,
  CustomerDetail,
  CustomerTicket,
  InboxQuery,
  Page,
  Profile,
  PublicMessage,
  Schemas,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public requestId?: string,
    public mutation = false,
  ) {
    super(errorMessage(status, code, mutation));
    this.name = "ApiError";
  }
}
export function errorMessage(
  status: number,
  code: string,
  mutation = false,
): string {
  const messages: Record<string, string> = {
    account_inactive:
      "This account is inactive. Contact the project administrator.",
    ticket_already_assigned:
      "Another agent may have claimed this ticket. Refresh to see the current assignment.",
    ticket_resolved:
      "This ticket is resolved. Reopen it before sending a public reply.",
    invalid_transition:
      "The ticket status has changed or this transition is unavailable. Refresh before continuing.",
    invalid_assignee:
      "That agent is unavailable. Refresh the agent directory and choose an active agent.",
    queue_changed:
      "The queue changed while loading. Refresh to calculate complete totals.",
    invalid_projection:
      "The ticket response could not be displayed safely. Refresh or contact the administrator.",
    configuration:
      "Frontend configuration is incomplete or invalid. Review the public environment variable names in the setup guide.",
  };
  if (messages[code]) return messages[code];
  if (status === 401)
    return "Your session is no longer valid. Sign out and sign in again.";
  if (status === 403)
    return "You do not have access to this action. Refresh your account or sign in with the correct account.";
  if (status === 404)
    return "This ticket is unavailable. Check the link or return to your ticket list.";
  if (status === 409)
    return "The ticket changed during this action. Refresh before trying again.";
  if (status === 422)
    return "Check the submitted fields. Text must be within the stated limits and cannot contain unsafe control characters.";
  if (mutation && (status === 0 || status >= 500))
    return "The change was not confirmed and may have reached the server. Check the ticket before submitting again.";
  if (status >= 500) return "Support is temporarily unavailable. Please retry.";
  return "The connection could not be completed. Check your network and try again.";
}
export function safeError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, "network_error");
}
export interface TokenSource {
  readonly uid?: string;
  getIdToken(forceRefresh?: boolean): Promise<string>;
}
type Query = Record<string, string | number | boolean | null | undefined>;
export function queryString(query: Query = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query))
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
export function validOrigin(value: string | undefined): string {
  try {
    const url = new URL(value || "");
    if (
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      !(
        url.protocol === "https:" ||
        (url.protocol === "http:" &&
          ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
      )
    )
      throw new Error();
    return url.origin;
  } catch {
    throw new ApiError(0, "configuration");
  }
}
export class ApiClient {
  constructor(
    private base: string,
    private identity: () => TokenSource | null,
    private sessionError: (reason?: ApiError) => void = () => {},
  ) {}
  async request<T>(
    path: string,
    options: {
      method?: "GET" | "POST" | "PATCH";
      body?: unknown;
      signal?: AbortSignal;
    } = {},
  ): Promise<T> {
    const origin = validOrigin(this.base);
    if (!path.startsWith("/api/v1/") || path.startsWith("//"))
      throw new ApiError(0, "configuration");
    const user = this.identity();
    if (!user) throw new ApiError(401, "signed_out"); // Never send an unauthenticated operational request.
    const mutation = !!options.method && options.method !== "GET";
    for (let attempt = 0; attempt < 2; attempt++) {
      options.signal?.throwIfAborted();
      const current = this.identity();
      if (!current || (user.uid && current.uid !== user.uid))
        throw new ApiError(401, "session_changed");
      let token: string;
      try {
        token = await user.getIdToken(attempt === 1);
        if (!token) throw new Error();
      } catch {
        if (this.identity()?.uid === user.uid)
          this.sessionError(new ApiError(401, "session_expired"));
        throw new ApiError(401, "session_expired");
      }
      options.signal?.throwIfAborted();
      if (!this.identity() || (user.uid && this.identity()?.uid !== user.uid))
        throw new ApiError(401, "session_changed");
      let response: Response;
      try {
        response = await fetch(origin + path, {
          method: options.method || "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
            ...(options.body !== undefined
              ? { "Content-Type": "application/json" }
              : {}),
          },
          ...(options.body !== undefined
            ? { body: JSON.stringify(options.body) }
            : {}),
          signal: options.signal,
          cache: "no-store",
          credentials: "omit",
          redirect: "error",
        });
      } catch (error) {
        if (options.signal?.aborted) throw error;
        throw new ApiError(0, "network_error", undefined, mutation);
      }
      if (!this.identity() || (user.uid && this.identity()?.uid !== user.uid))
        throw new ApiError(401, "session_changed");
      if (response.status === 401 && attempt === 0) continue;
      if (response.status === 401)
        this.sessionError(new ApiError(401, "session_expired"));
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        throw new ApiError(
          response.status >= 400 ? response.status : 502,
          "invalid_response",
          undefined,
          mutation,
        );
      }
      if (!response.ok) {
        const record = data as {
          error?: { code?: unknown };
          request_id?: unknown;
        };
        const code =
          typeof record?.error?.code === "string" &&
          /^[a-z_]{1,60}$/.test(record.error.code)
            ? record.error.code
            : "request_failed";
        const requestId =
          typeof record?.request_id === "string" &&
          /^[\da-f-]{36}$/i.test(record.request_id)
            ? record.request_id
            : undefined;
        if (code === "account_inactive")
          this.sessionError(new ApiError(response.status, code));
        throw new ApiError(response.status, code, requestId, mutation);
      }
      return data as T;
    }
    throw new ApiError(401, "session_expired");
  }
  me(signal?: AbortSignal) {
    return this.request<Profile>("/api/v1/me", { signal });
  }
  onboard(signal?: AbortSignal) {
    return this.request<Profile>("/api/v1/auth/onboard", {
      method: "POST",
      body: {},
      signal,
    });
  }
  customerTickets(page = 1, signal?: AbortSignal) {
    return this.request<Page<CustomerTicket>>(
      "/api/v1/tickets" + queryString({ page, page_size: 25 }),
      { signal },
    );
  }
  customerPage(page: number, signal?: AbortSignal) {
    return this.request<Page<CustomerTicket>>(
      "/api/v1/tickets" + queryString({ page, page_size: 100 }),
      { signal },
    );
  }
  createTicket(body: Schemas["TicketCreate"]) {
    return this.request<CustomerTicket>("/api/v1/tickets", {
      method: "POST",
      body,
    });
  }
  async customerDetail(id: string, query: Query = {}, signal?: AbortSignal) {
    const data = await this.request<CustomerDetail>(
      `/api/v1/tickets/${encodeURIComponent(id)}` + queryString(query),
      { signal },
    );
    if (data.messages.items.some((item) => item.visibility !== "PUBLIC"))
      throw new ApiError(502, "invalid_projection");
    return data;
  }
  customerReply(id: string, body: Schemas["CustomerMessageCreate"]) {
    return this.request<PublicMessage>(
      `/api/v1/tickets/${encodeURIComponent(id)}/messages`,
      { method: "POST", body },
    );
  }
  reopen(id: string) {
    return this.request<CustomerTicket>(
      `/api/v1/tickets/${encodeURIComponent(id)}/reopen`,
      { method: "POST", body: {} },
    );
  }
  inbox(query: InboxQuery = {}, signal?: AbortSignal) {
    return this.request<Page<AgentTicket>>(
      "/api/v1/agent/tickets" + queryString(query),
      { signal },
    );
  }
  agentDetail(id: string, query: Query = {}, signal?: AbortSignal) {
    return this.request<AgentDetail>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}` + queryString(query),
      { signal },
    );
  }
  agents(page = 1, signal?: AbortSignal) {
    return this.request<Page<Agent>>(
      "/api/v1/agent/users" + queryString({ page, page_size: 100 }),
      { signal },
    );
  }
  claim(id: string) {
    return this.request<AgentTicket>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}/claim`,
      { method: "POST", body: {} },
    );
  }
  assignment(id: string, body: Schemas["AssignmentChange"]) {
    return this.request<AgentTicket>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}/assignment`,
      { method: "PATCH", body },
    );
  }
  status(id: string, body: Schemas["StatusChange"]) {
    return this.request<AgentTicket>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}/status`,
      { method: "PATCH", body },
    );
  }
  priority(id: string, body: Schemas["PriorityChange"]) {
    return this.request<AgentTicket>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}/priority`,
      { method: "PATCH", body },
    );
  }
  agentReply(id: string, body: Schemas["AgentMessageCreate"]) {
    return this.request<AgentMessage>(
      `/api/v1/agent/tickets/${encodeURIComponent(id)}/messages`,
      { method: "POST", body },
    );
  }
}
export async function resolveProfile(
  api: Pick<ApiClient, "me" | "onboard">,
  signal?: AbortSignal,
): Promise<Profile> {
  let profile: Profile;
  try {
    profile = await api.me(signal);
  } catch (error) {
    if (
      !(error instanceof ApiError) ||
      error.status !== 403 ||
      error.code !== "onboarding_required"
    )
      throw error;
    await api.onboard(signal);
    profile = await api.me(signal);
  }
  if (!profile.is_active) throw new ApiError(403, "account_inactive");
  if (!["CUSTOMER", "AGENT"].includes(profile.role))
    throw new ApiError(403, "invalid_role");
  return profile;
}
