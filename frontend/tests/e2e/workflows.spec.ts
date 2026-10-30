import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { mockServices, signIn, stateFor, uuid } from "./fixture";
const browserErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  browserErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration/i.test(message.text()))
      errors.push(message.text());
  });
});
test.afterEach(async ({ page }) => {
  expect(browserErrors.get(page) || []).toEqual([]);
});
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}
async function accessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(
    result.violations.map((v) => ({
      id: v.id,
      targets: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
}
async function picture(page: Page, name: string, project: string) {
  const folder = path.resolve("../artifacts/m3/visuals");
  await mkdir(folder, { recursive: true });
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement)
      document.activeElement.blur();
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  });
  await page.screenshot({
    path: path.join(folder, `${project}-${name}.png`),
    fullPage: true,
  });
}

test("public introduction, responsive layout, and sign-in labels", async ({
  page,
}, info) => {
  const state = stateFor();
  await mockServices(page, state);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "A little clarity. Back to listening." }),
  ).toBeVisible();
  await noOverflow(page);
  await accessible(page);
  await picture(page, "landing", info.project.name);
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Email address")).toBeVisible();
  await accessible(page);
  await picture(page, "sign-in", info.project.name);
});

test("verified-email gating, normal verification request, and onboarding", async ({
  page,
}) => {
  const state = stateFor();
  state.emailVerified = false;
  state.onboarded = false;
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/verify-email$/);
  expect(state.requests).toHaveLength(0);
  await page.getByRole("button", { name: "Send verification email" }).click();
  await expect.poll(() => state.oobCalls).toBe(1);
  state.emailVerified = true;
  await page.getByRole("button", { name: "I’ve verified my email" }).click();
  await expect(page).toHaveURL(/\/customer$/);
  await expect(
    page.getByRole("heading", { name: "Good to see you." }),
  ).toBeVisible();
  expect(state.onboardCalls).toBe(1);
  expect(
    state.requests.find((r) => r.path === "/api/v1/auth/onboard")?.body,
  ).toEqual({});
});

test("customer totals use every page; pagination, session restore, and role separation", async ({
  page,
}, info) => {
  const state = stateFor("CUSTOMER", 103);
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/customer$/);
  await expect(page.locator(".stat-card").first()).toContainText("103");
  expect(state.requests.some((r) => r.path.includes("page=2"))).toBe(true);
  await picture(page, "customer-overview", info.project.name);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Good to see you." }),
  ).toBeVisible();
  await page.goto("/customer/tickets");
  await expect(
    page.getByRole("heading", { name: "My tickets", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Next tickets page" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.locator(".ticket-row")).toHaveCount(25);
  await page.goto("/agent/tickets");
  await expect(page).toHaveURL(/\/customer$/);
  expect(state.requests.some((r) => r.path.startsWith("/api/v1/agent/"))).toBe(
    false,
  );
  await noOverflow(page);
});

test("customer creates, replies, reopens, and never sees internal content", async ({
  page,
}, info) => {
  const state = stateFor();
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/customer$/);
  await page.goto("/customer/tickets/new");
  await page.getByLabel("Subject", { exact: true }).fill("   ");
  await page.getByLabel("Tell us a little more").fill("Synthetic description");
  await page
    .getByRole("button", { name: "Create request", exact: true })
    .click();
  await expect(page.getByText("Please add some text.")).toBeVisible();
  expect(
    state.requests.filter(
      (r) => r.method === "POST" && r.path === "/api/v1/tickets",
    ),
  ).toHaveLength(0);
  await page
    .getByLabel("Subject", { exact: true })
    .fill("Synthetic: Playback pauses on my test device");
  await page
    .getByRole("button", { name: "Create request", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Synthetic: Playback pauses on my test device",
    }),
  ).toBeVisible();
  expect(
    state.requests.filter(
      (r) => r.method === "POST" && r.path === "/api/v1/tickets",
    ),
  ).toHaveLength(1);
  await page
    .getByRole("textbox", { name: "Public reply" })
    .fill("Synthetic: additional reproduction context.");
  await page.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(
    page
      .getByRole("list", { name: "Conversation messages" })
      .getByText("Synthetic: additional reproduction context.", {
        exact: true,
      }),
  ).toBeVisible();
  const ticket = state.tickets.at(-1)!;
  ticket.status = "RESOLVED";
  ticket.resolved_at = "2026-01-01T18:00:00Z";
  ticket.messages.push({
    id: uuid(55555),
    author_id: uuid(9002),
    author: { display_name: "Synthetic Agent", kind: "AGENT" },
    body: "INTERNAL_SYNTHETIC_ONLY",
    visibility: "INTERNAL",
    created_at: "2026-01-01T18:00:00Z",
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Reopen request" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send reply", exact: true }),
  ).toBeDisabled();
  await expect(page.getByText("INTERNAL_SYNTHETIC_ONLY")).toHaveCount(0);
  await page.getByRole("button", { name: "Reopen request" }).click();
  await expect(
    page.getByRole("button", { name: "Reopen request" }),
  ).toHaveCount(0);
  await expect(page.locator(".ticket-info .badge").first()).toHaveText(
    "Reopened",
  );
  await noOverflow(page);
  await accessible(page);
  await picture(page, "customer-ticket", info.project.name);
});

test("agent filters, assignment, lifecycle, and claim conflict", async ({
  page,
}, info) => {
  const state = stateFor("AGENT");
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/agent$/);
  await page.goto("/agent/tickets");
  await page.getByLabel("Status", { exact: true }).selectOption("OPEN");
  await page
    .getByLabel("Assignment", { exact: true })
    .selectOption("unassigned");
  await page.getByLabel("Subject or full ticket ID").fill("question 1");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(/unassigned_only=true/);
  await expect(page.locator(".ticket-row")).toHaveCount(1);
  await page
    .getByRole("link", { name: "Synthetic: Playback question 1" })
    .click();
  await page.getByRole("button", { name: "Claim ticket", exact: true }).click();
  await expect(page.locator(".workspace-ticket-summary")).toContainText(
    "Assigned to you",
  );
  expect(state.tickets[0].status).toBe("OPEN");
  await page.getByRole("button", { name: "Start work", exact: true }).click();
  await expect(page.locator(".control-heading")).toContainText("In progress");
  await page.getByLabel("Priority", { exact: true }).selectOption("HIGH");
  await expect.poll(() => state.tickets[0].priority).toBe("HIGH");
  await expect(page.getByLabel("Priority", { exact: true })).toBeEnabled();
  await page.getByLabel("Assigned agent").selectOption(uuid(9003));
  await expect(page.locator(".workspace-ticket-summary")).toContainText(
    "Synthetic Second Agent",
  );
  await page
    .getByRole("button", { name: "Unassign ticket", exact: true })
    .click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(state.tickets[0].assigned_agent_id).toBe(uuid(9003));
  await page
    .getByRole("button", { name: "Unassign ticket", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Unassign ticket", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Claim ticket", exact: true }),
  ).toBeVisible();
  state.forceClaimConflict = true;
  const before = state.requests.filter((r) => r.path.endsWith("/claim")).length;
  await page.getByRole("button", { name: "Claim ticket", exact: true }).click();
  await expect(
    page.getByText(
      "Another agent may have claimed this ticket. Refresh to see the current assignment.",
    ),
  ).toBeVisible();
  expect(
    state.requests.filter((r) => r.path.endsWith("/claim")).length - before,
  ).toBe(1);
  await page.getByRole("button", { name: "Refresh data", exact: true }).click();
  await expect(page.locator(".workspace-ticket-summary")).toContainText(
    "Synthetic Second Agent",
  );
  await noOverflow(page);
  await accessible(page);
  await picture(page, "agent-workspace", info.project.name);
  await page.getByRole("link", { name: "Back to inbox" }).click();
  await expect(page).toHaveURL(/unassigned_only=true/);
  await page.getByRole("link", { name: "Reset", exact: true }).click();
  await expect(page.locator(".ticket-row")).toHaveCount(3);
  await noOverflow(page);
  await accessible(page);
  await picture(page, "agent-inbox", info.project.name);
});

test("agent public/internal drafts, resolved notes, and reopen", async ({
  page,
}, info) => {
  const state = stateFor("AGENT");
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/agent$/);
  await page.goto(`/agent/tickets/${uuid(1)}`);
  await page
    .getByRole("textbox", { name: "Public reply" })
    .fill("Synthetic public response retained independently.");
  await page.getByRole("tab", { name: "Public reply" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "Internal note" }),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("textbox", { name: "Internal note" })
    .fill("Synthetic private investigation.");
  await page.getByRole("button", { name: "Save internal note" }).click();
  await expect(
    page
      .getByRole("list", { name: "Conversation messages" })
      .getByText("Synthetic private investigation.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Internal note" }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Public reply" }).click();
  await expect(page.getByRole("textbox", { name: "Public reply" })).toHaveValue(
    "Synthetic public response retained independently.",
  );
  await page
    .getByRole("button", { name: "Resolve ticket", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Resolve ticket", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Reopen ticket", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send reply", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "Internal note" }).click();
  await page
    .getByRole("textbox", { name: "Internal note" })
    .fill("Synthetic internal follow-up after resolution.");
  await page.getByRole("button", { name: "Save internal note" }).click();
  await expect(
    page
      .getByRole("list", { name: "Conversation messages" })
      .getByText("Synthetic internal follow-up after resolution.", {
        exact: true,
      }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Reopen ticket", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Reopen ticket", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Public reply" }).click();
  await page.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(
    page
      .getByRole("list", { name: "Conversation messages" })
      .getByText("Synthetic public response retained independently.", {
        exact: true,
      }),
  ).toBeVisible();
  expect(state.tickets[0].messages.map((m) => m.visibility)).toEqual([
    "INTERNAL",
    "INTERNAL",
    "PUBLIC",
  ]);
  await page.getByRole("tab", { name: "Internal note" }).click();
  await picture(page, "agent-notes", info.project.name);
  await accessible(page);
});

test("sign-up requests verification without premature onboarding", async ({
  page,
}) => {
  const state = stateFor("CUSTOMER", 0);
  state.emailVerified = false;
  state.onboarded = false;
  await mockServices(page, state);
  await page.goto("/sign-up");
  await page.getByLabel("Email address").fill("synthetic@example.invalid");
  await page
    .getByLabel("Password", { exact: true })
    .fill("SyntheticPassword123!");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page).toHaveURL(/\/verify-email$/);
  await expect.poll(() => state.oobCalls).toBe(1);
  expect(state.requests).toHaveLength(0);
});

test("empty queue guidance and expired-session data clearing", async ({
  page,
}) => {
  const state = stateFor("CUSTOMER", 0);
  await mockServices(page, state);
  await signIn(page);
  await expect(
    page.getByRole("heading", { name: "Let’s start with your question." }),
  ).toBeVisible();
  expect(await page.locator(".stat-card>strong").allTextContents()).toEqual([
    "0",
    "0",
    "0",
  ]);
  state.forceUnauthorized = true;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Account access" }),
  ).toBeVisible();
  await expect(page.locator(".stat-card")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Sign out", exact: true }),
  ).toBeVisible();
});

test("inactive account, network failure, and unavailable ticket are actionable", async ({
  page,
}) => {
  const state = stateFor();
  state.profile.is_active = false;
  await mockServices(page, state);
  await signIn(page);
  await expect(
    page.getByText(
      "This account is inactive. Contact the project administrator.",
    ),
  ).toBeVisible();
  expect(state.requests.some((r) => r.path.startsWith("/api/v1/tickets"))).toBe(
    false,
  );
  state.profile.is_active = true;
  await page.getByRole("button", { name: "Refresh account" }).click();
  await expect(page).toHaveURL(/\/customer$/);
  await page.goto(`/customer/tickets/${uuid(99999)}`);
  await expect(
    page.getByText(
      "This ticket is unavailable. Check the link or return to your ticket list.",
    ),
  ).toBeVisible();
  await page.goto("/customer/tickets");
  await page.route("http://127.0.0.1:8900/api/v1/tickets**", (route) =>
    route.abort(),
  );
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByText(
      "The connection could not be completed. Check your network and try again.",
    ),
  ).toBeVisible();
});

test("mobile navigation keyboard focus and sign-out", async ({
  page,
}, info) => {
  const state = stateFor();
  await mockServices(page, state);
  await signIn(page);
  await expect(page).toHaveURL(/\/customer$/);
  if (info.project.name === "mobile") {
    const open = page.getByRole("button", { name: "Open navigation" });
    await open.click();
    const dialog = page.getByRole("dialog", { name: "Main navigation" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("link", { name: "My tickets", exact: true }),
    ).toBeVisible();
    await accessible(page);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(open).toBeFocused();
    await open.click();
    await dialog.getByRole("button", { name: "Sign out", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
  }
  await expect(page).toHaveURL(/\/sign-in$/);
});
