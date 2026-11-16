import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockServices, signIn, stateFor, uuid } from "./fixture";
import type { Schemas } from "../../src/lib/api/types";

test("agent can edit and approve evidence draft without sending a reply", async ({
  page,
}) => {
  const state = stateFor("AGENT");
  await mockServices(page, state);
  const ticketId = uuid(1);
  const draftId = uuid(8100);
  let draft: Schemas["DraftRun"] | undefined;
  const firstLine = "The fictional player reports a pause in the trace.";
  const secondLine = "The sample trace records the timestamp.";
  let reviewCount = 0;
  await page.route(
    /http:\/\/127\.0\.0\.1:8900\/api\/v1\/agent\/tickets\/[^/]+\/drafts/,
    async (route) => {
      const request = route.request();
      const headers = {
        "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
        "Access-Control-Allow-Headers": "Authorization,Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      };
      if (request.method() === "OPTIONS")
        return route.fulfill({ status: 204, headers });
      const send = (json: unknown, status = 200) =>
        route.fulfill({ status, json, headers });
      if (request.method() === "GET") {
        return send({
          ticket_id: ticketId,
          items: draft ? [draft] : [],
          total: draft ? 1 : 0,
          page: 1,
          generation_enabled: true,
        });
      }
      const body = request.postDataJSON();
      if (!draft) {
        expect(body.expected_run_id).toBeNull();
        const now = "2026-01-01T00:00:00.000Z";
        draft = {
          id: draftId,
          ticket_id: ticketId,
          state: "DRAFT",
          model: "synthetic-model",
          prompt_version: "synthetic-v1",
          text: `Thanks for reaching out.\n${firstLine}\n${secondLine}`,
          synthetic: true,
          validation: "SUPPORTED_EXTRACTIVE",
          failure: null,
          retrieval_method: "hybrid",
          provider_calls: 1,
          usage: { totalTokenCount: 28 },
          latency_ms: 4,
          version: 1,
          created_at: now,
          completed_at: now,
          reviewed_at: null,
          reviewed_by: null,
          evidence: [
            {
              article_id: uuid(8110),
              revision: 1,
              revision_id: uuid(8111),
              chunk_id: uuid(8112),
              content_hash: "0".repeat(64),
              title: "Fictional playback trace",
              excerpt: firstLine + " " + secondLine,
              source_name: "Synthetic fixture",
              source_type: "SYNTHETIC_TEST",
              source_url: null,
              method: "hybrid",
              rank: 1,
              score: 0.02,
              score_type: "RECIPROCAL_RANK_FUSION",
              lexical_rank: 1,
              semantic_rank: 1,
              embedding_model: "synthetic",
            },
          ],
        };
        return send(draft, 202);
      }
      expect(body.expected_version).toBe(draft.version);
      reviewCount += 1;
      if (body.action === "EDIT")
        draft = { ...draft, text: body.text, version: 2 };
      else if (body.action === "APPROVE")
        draft = { ...draft, state: "APPROVED", version: 3 };
      return send(draft);
    },
  );
  await signIn(page);
  await page.goto(`/agent/tickets/${ticketId}`);
  const panel = page.getByRole("region", { name: "AI draft" });
  await panel.getByRole("button", { name: "Open AI drafts" }).click();
  await panel
    .getByRole("button", { name: "Generate draft", exact: true })
    .press("Enter");
  await expect(panel.getByText("Ready for review")).toBeVisible();
  await panel.getByText("Evidence (1)", { exact: true }).click();
  await expect(
    panel.getByRole("link", { name: "Fictional playback trace" }),
  ).toHaveAttribute("href", `/agent/knowledge/${uuid(8110)}?revision=1`);
  await panel.getByRole("button", { name: "Edit draft" }).click();
  await panel
    .getByLabel("Draft text")
    .fill(`Thanks for reaching out.\n${firstLine}`);
  await panel.getByRole("button", { name: "Save edit" }).click();
  await panel
    .getByRole("button", { name: "Approve draft", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Approve draft", exact: true })
    .click();
  await expect(panel.getByText("Approved for agent reference")).toBeVisible();
  expect(reviewCount).toBe(2);
  expect(
    state.requests.filter(
      (row) => row.method === "POST" && row.path.endsWith("/messages"),
    ),
  ).toHaveLength(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
});

test("customer ticket never loads draft content or controls", async ({
  page,
}) => {
  const state = stateFor("CUSTOMER");
  await mockServices(page, state);
  await signIn(page);
  await page.goto(`/customer/tickets/${uuid(1)}`);
  await expect(
    page.getByRole("heading", { name: "Synthetic: Playback question 1" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "AI draft" })).toHaveCount(0);
  expect(state.requests.some((row) => row.path.includes("/drafts"))).toBe(
    false,
  );
});
