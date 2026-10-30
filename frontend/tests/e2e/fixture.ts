// Synthetic external-service responses only. This file is never imported by application code.
import type { Page as BrowserPage, Route } from "@playwright/test";
import type {
  AgentDetail,
  AgentEvent,
  AgentMessage,
  AgentTicket,
  CustomerDetail,
  CustomerTicket,
  Page,
  Profile,
  PublicEvent,
  PublicMessage,
  Role,
  Status,
  Visibility,
} from "../../src/lib/api/types";
import { transitions } from "../../src/lib/tickets";
export const uuid = (n: number) =>
  `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
type RecordTicket = AgentTicket & {
  description: string;
  messages: AgentMessage[];
  events: AgentEvent[];
};
const baseTime = "2026-01-01T12:00:00.000Z";
export function stateFor(role: Role = "CUSTOMER", count = 3) {
  const profile: Profile = {
    id: uuid(role === "AGENT" ? 9002 : 9001),
    display_name: `Synthetic ${role === "AGENT" ? "Agent" : "Customer"}`,
    email: "synthetic@example.invalid",
    role,
    is_active: true,
    created_at: baseTime,
  };
  const tickets: RecordTicket[] = Array.from({ length: count }, (_, i) => ({
    id: uuid(i + 1),
    customer_id: uuid(9001),
    assigned_agent_id: null,
    subject: `Synthetic: Playback question ${i + 1}`,
    description: "Synthetic reproduction steps. No real customer information.",
    status: "OPEN",
    priority: "NORMAL",
    created_at: new Date(Date.parse(baseTime) + i * 60000).toISOString(),
    updated_at: new Date(Date.parse(baseTime) + i * 60000).toISOString(),
    resolved_at: null,
    messages: [],
    events: [
      {
        id: uuid(10000 + i),
        event_type: "TICKET_CREATED",
        action: "TICKET_CREATED",
        actor_id: uuid(9001),
        metadata: {},
        created_at: baseTime,
      },
    ],
  }));
  return {
    profile,
    tickets,
    emailVerified: true,
    onboarded: true,
    onboardCalls: 0,
    oobCalls: 0,
    forceClaimConflict: false,
    forceUnauthorized: false,
    requests: [] as {
      method: string;
      path: string;
      body: Record<string, unknown>;
    }[],
    tick: 0,
  };
}
export type State = ReturnType<typeof stateFor>;
function token(state: State) {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return [
    encode({ alg: "RS256", typ: "JWT" }),
    encode({
      iss: "https://securetoken.google.com/synthetic-supportlens",
      aud: "synthetic-supportlens",
      sub: `synthetic-${state.profile.role.toLowerCase()}`,
      user_id: `synthetic-${state.profile.role.toLowerCase()}`,
      email: state.profile.email,
      email_verified: state.emailVerified,
      iat: Math.floor(Date.now() / 1000),
      auth_time: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
      role: "AGENT",
      firebase: {
        sign_in_provider: "password",
        identities: { email: [state.profile.email] },
      },
    }),
    "synthetic-signature",
  ].join(".");
}
function customerSummary(t: RecordTicket): CustomerTicket {
  return {
    id: t.id,
    subject: t.subject,
    status: t.status,
    priority: t.priority,
    created_at: t.created_at,
    updated_at: t.updated_at,
    resolved_at: t.resolved_at,
  };
}
function agentSummary(t: RecordTicket): AgentTicket {
  return {
    ...customerSummary(t),
    customer_id: t.customer_id,
    assigned_agent_id: t.assigned_agent_id,
  };
}
function page<T>(items: T[], number: number, size: number): Page<T> {
  return {
    items: items.slice((number - 1) * size, number * size),
    total: items.length,
    page: number,
    page_size: size,
  };
}
export async function mockServices(browser: BrowserPage, state: State) {
  // Fail closed for unexpected external requests. The mock API origin is deliberately not the real development backend.
  await browser.route("**/*", (route) =>
    new URL(route.request().url()).origin === "http://127.0.0.1:3100"
      ? route.continue()
      : route.abort(),
  );
  await browser.route(
    "https://identitytoolkit.googleapis.com/**",
    async (route) => {
      const cors = {
        "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers":
          route.request().headers()["access-control-request-headers"] ||
          "content-type,x-client-version,x-firebase-client",
      };
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: cors });
        return;
      }
      const path = new URL(route.request().url()).pathname;
      const uid = `synthetic-${state.profile.role.toLowerCase()}`;
      const user = {
        localId: uid,
        email: state.profile.email,
        emailVerified: state.emailVerified,
        displayName: state.profile.display_name,
        providerUserInfo: [
          {
            providerId: "password",
            rawId: state.profile.email,
            email: state.profile.email,
          },
        ],
        createdAt: "1767268800000",
        lastLoginAt: "1767268800000",
      };
      let data: unknown;
      if (
        path.endsWith("accounts:signInWithPassword") ||
        path.endsWith("accounts:signUp")
      )
        data = {
          localId: uid,
          email: state.profile.email,
          displayName: state.profile.display_name,
          idToken: token(state),
          refreshToken: "synthetic-refresh",
          expiresIn: "3600",
          registered: true,
        };
      else if (path.endsWith("accounts:lookup")) data = { users: [user] };
      else if (path.endsWith("accounts:sendOobCode")) {
        state.oobCalls++;
        data = { email: state.profile.email };
      } else if (path.endsWith("recaptchaConfig"))
        data = { recaptchaEnforcementState: [] };
      else {
        await route.abort();
        return;
      }
      await route.fulfill({ json: data, headers: cors });
    },
  );
  await browser.route("https://securetoken.googleapis.com/**", (route) => {
    const headers = {
      "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
      "Access-Control-Allow-Methods": "POST,OPTIONS",
      "Access-Control-Allow-Headers":
        route.request().headers()["access-control-request-headers"] ||
        "content-type,x-client-version",
    };
    return route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers })
      : route.fulfill({
          headers,
          json: {
            access_token: token(state),
            id_token: token(state),
            refresh_token: "synthetic-refresh",
            expires_in: "3600",
            token_type: "Bearer",
            user_id: `synthetic-${state.profile.role.toLowerCase()}`,
            project_id: "synthetic-supportlens",
          },
        });
  });
  await browser.route("http://127.0.0.1:8900/**", (route) => api(route, state));
}
async function api(route: Route, state: State) {
  const request = route.request(),
    url = new URL(request.url()),
    path = url.pathname,
    method = request.method();
  const headers = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
  };
  const send = (data: unknown, status = 200) =>
    route.fulfill({ status, json: data, headers });
  const error = (status: number, code: string) =>
    send(
      {
        error: { code, message: "Synthetic API response" },
        request_id: uuid(9999),
      },
      status,
    );
  if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
  const body = (request.postDataJSON() || {}) as Record<string, unknown>;
  state.requests.push({ method, path: path + url.search, body });
  if (!request.headers().authorization?.startsWith("Bearer "))
    return error(401, "invalid_credentials");
  if (state.forceUnauthorized) return error(401, "invalid_credentials");
  if (!state.profile.is_active) return error(403, "account_inactive");
  if (path === "/api/v1/me")
    return state.onboarded
      ? send(state.profile)
      : error(403, "onboarding_required");
  if (path === "/api/v1/auth/onboard") {
    if (Object.keys(body).length) return error(422, "invalid_request");
    state.onboarded = true;
    state.onboardCalls++;
    return send(state.profile);
  }
  const agent = path.startsWith("/api/v1/agent/");
  if (agent !== (state.profile.role === "AGENT"))
    return error(403, "forbidden");
  const size = Number(url.searchParams.get("page_size") || 25),
    number = Number(url.searchParams.get("page") || 1);
  if (path === "/api/v1/agent/users")
    return send(
      page(
        [
          { id: uuid(9002), display_name: "Synthetic Agent" },
          { id: uuid(9003), display_name: "Synthetic Second Agent" },
        ],
        number,
        size,
      ),
    );
  const base = agent ? "/api/v1/agent/tickets" : "/api/v1/tickets";
  const now = () =>
    new Date(
      Date.parse(baseTime) + 20000000 + ++state.tick * 1000,
    ).toISOString();
  function event(
    t: RecordTicket,
    action: AgentEvent["action"],
    metadata: Record<string, unknown> = {},
  ) {
    t.events.push({
      id: uuid(20000 + state.tick + t.events.length),
      event_type:
        action === "STATUS_CHANGED" || action === "REOPENED"
          ? "STATUS_CHANGED"
          : action === "ASSIGNMENT_CHANGED"
            ? "ASSIGNMENT_CHANGED"
            : action === "TICKET_CREATED"
              ? "TICKET_CREATED"
              : "REPLY_ADDED",
      action,
      actor_id: state.profile.id,
      metadata,
      created_at: now(),
    });
  }
  if (path === base && method === "POST" && !agent) {
    if (
      Object.keys(body).some((k) => !["subject", "description"].includes(k)) ||
      typeof body.subject !== "string" ||
      !body.subject.trim() ||
      typeof body.description !== "string" ||
      !body.description.trim()
    )
      return error(422, "invalid_request");
    const t: RecordTicket = {
      id: uuid(500 + state.tickets.length),
      subject: body.subject.trim(),
      description: body.description.trim(),
      customer_id: state.profile.id,
      assigned_agent_id: null,
      status: "OPEN",
      priority: "NORMAL",
      created_at: now(),
      updated_at: now(),
      resolved_at: null,
      messages: [],
      events: [],
    };
    event(t, "TICKET_CREATED");
    state.tickets.push(t);
    return send(customerSummary(t), 201);
  }
  if (path === base && method === "GET") {
    let items = [...state.tickets].filter(
      (t) => agent || t.customer_id === state.profile.id,
    );
    for (const field of ["status", "priority", "assigned_agent_id"] as const) {
      const value = url.searchParams.get(field);
      if (value) items = items.filter((t) => t[field] === value);
    }
    if (url.searchParams.get("unassigned_only") === "true")
      items = items.filter((t) => !t.assigned_agent_id);
    const search = url.searchParams.get("search");
    if (search)
      items = items.filter(
        (t) =>
          t.subject.toLowerCase().includes(search.toLowerCase()) ||
          t.id === search,
      );
    items.sort(
      (a, b) =>
        (url.searchParams.get("order") === "oldest" ? 1 : -1) *
        a.created_at.localeCompare(b.created_at),
    );
    return send(
      page(
        items.map((t) => (agent ? agentSummary(t) : customerSummary(t))),
        number,
        size,
      ),
    );
  }
  const remainder = path.startsWith(base + "/")
    ? path.slice(base.length + 1).split("/")
    : [];
  const t = state.tickets.find((t) => t.id === remainder[0]);
  if (!t || (!agent && t.customer_id !== state.profile.id))
    return error(404, "ticket_not_found");
  if (remainder.length === 1 && method === "GET") {
    const messagePage = Number(url.searchParams.get("messages_page") || 1),
      eventPage = Number(
        url.searchParams.get(agent ? "events_page" : "history_page") || 1,
      );
    if (agent) {
      const data: AgentDetail = {
        ...agentSummary(t),
        description: t.description,
        messages: page(t.messages, messagePage, size),
        events: page(t.events, eventPage, size),
      };
      return send(data);
    }
    const messages: PublicMessage[] = t.messages
      .filter((m) => m.visibility === "PUBLIC")
      .map((m) => ({
        id: m.id,
        body: m.body,
        visibility: "PUBLIC",
        author: m.author,
        created_at: m.created_at,
      }));
    const history: PublicEvent[] = t.events
      .filter((e) =>
        [
          "TICKET_CREATED",
          "CUSTOMER_REPLY",
          "AGENT_REPLY",
          "STATUS_CHANGED",
          "REOPENED",
          "PRIORITY_CHANGED",
        ].includes(e.action),
      )
      .map((e) => ({
        id: e.id,
        action: e.action as PublicEvent["action"],
        created_at: e.created_at,
      }));
    const data: CustomerDetail = {
      ...customerSummary(t),
      description: t.description,
      messages: page(messages, messagePage, size),
      history: page(history, eventPage, size),
    };
    return send(data);
  }
  const action = remainder[1];
  if (action === "messages" && method === "POST") {
    if (
      Object.keys(body).some(
        (k) => !(agent ? ["body", "visibility"] : ["body"]).includes(k),
      ) ||
      typeof body.body !== "string" ||
      !body.body.trim()
    )
      return error(422, "invalid_request");
    const visibility: Visibility =
      agent && body.visibility === "INTERNAL" ? "INTERNAL" : "PUBLIC";
    if (t.status === "RESOLVED" && visibility === "PUBLIC")
      return error(409, "ticket_resolved");
    const m: AgentMessage = {
      id: uuid(30000 + state.tick + t.messages.length),
      body: body.body,
      visibility,
      author_id: state.profile.id,
      author: {
        display_name: state.profile.display_name,
        kind: state.profile.role,
      },
      created_at: now(),
    };
    t.messages.push(m);
    event(
      t,
      visibility === "INTERNAL"
        ? "INTERNAL_NOTE"
        : agent
          ? "AGENT_REPLY"
          : "CUSTOMER_REPLY",
    );
    if (visibility === "PUBLIC") t.updated_at = now();
    return send(
      agent
        ? m
        : {
            id: m.id,
            body: m.body,
            visibility: "PUBLIC",
            author: m.author,
            created_at: m.created_at,
          },
      201,
    );
  }
  if (action === "reopen" && !agent && method === "POST") {
    if (t.status !== "RESOLVED") return error(409, "invalid_transition");
    t.status = "REOPENED";
    t.resolved_at = null;
    t.updated_at = now();
    event(t, "REOPENED", { after: { status: "REOPENED" } });
    return send(customerSummary(t));
  }
  if (!agent) return error(403, "forbidden");
  if (action === "claim" && method === "POST") {
    if (state.forceClaimConflict) {
      state.forceClaimConflict = false;
      t.assigned_agent_id = uuid(9003);
      event(t, "ASSIGNMENT_CHANGED", {
        after: { assigned_agent_id: t.assigned_agent_id },
      });
      return error(409, "ticket_already_assigned");
    }
    if (t.assigned_agent_id) return error(409, "ticket_already_assigned");
    t.assigned_agent_id = state.profile.id;
    event(t, "ASSIGNMENT_CHANGED", {
      after: { assigned_agent_id: t.assigned_agent_id },
    });
  } else if (action === "assignment" && method === "PATCH") {
    if (
      !("assigned_agent_id" in body) ||
      !(
        body.assigned_agent_id === null ||
        [uuid(9002), uuid(9003)].includes(String(body.assigned_agent_id))
      )
    )
      return error(422, "invalid_assignee");
    t.assigned_agent_id = body.assigned_agent_id as string | null;
    event(t, "ASSIGNMENT_CHANGED", {
      after: { assigned_agent_id: t.assigned_agent_id },
    });
  } else if (action === "status" && method === "PATCH") {
    const status = body.status as Status;
    if (!transitions[t.status].includes(status))
      return error(409, "invalid_transition");
    t.status = status;
    t.resolved_at = status === "RESOLVED" ? now() : null;
    event(t, status === "REOPENED" ? "REOPENED" : "STATUS_CHANGED", {
      after: { status },
    });
  } else if (action === "priority" && method === "PATCH") {
    if (!["LOW", "NORMAL", "HIGH"].includes(String(body.priority)))
      return error(422, "invalid_request");
    t.priority = body.priority as AgentTicket["priority"];
    event(t, "PRIORITY_CHANGED", { after: { priority: t.priority } });
  } else return error(404, "ticket_not_found");
  t.updated_at = now();
  return send(agentSummary(t));
}
export async function signIn(page: BrowserPage) {
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("synthetic@example.invalid");
  await page
    .getByLabel("Password", { exact: true })
    .fill("SyntheticPassword123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}
