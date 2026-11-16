import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockServices, signIn, stateFor, uuid } from "./fixture";
import type { Schemas } from "../../src/lib/api/types";

test("agent edits knowledge, retrieves revision evidence and unpublishes", async ({
  page,
}) => {
  await mockServices(page, stateFor("AGENT"));
  const id = uuid(7001);
  const revisionId = uuid(7002);
  const now = "2026-01-01T00:00:00Z";
  let article: Schemas["ArticleDetail"] | undefined;
  const cors = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
  };
  await page.route(
    /http:\/\/127\.0\.0\.1:8900\/api\/v1\/agent\/(knowledge|tickets\/[^/]+\/knowledge)/,
    async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (req.method() === "OPTIONS")
        return route.fulfill({ status: 204, headers: cors });
      const send = (json: unknown, status = 200) =>
        route.fulfill({ status, headers: cors, json });
      if (
        url.pathname === "/api/v1/agent/knowledge" &&
        req.method() === "GET"
      ) {
        return send({
          items: article ? [article] : [],
          page: 1,
          page_size: 20,
          total: article ? 1 : 0,
        });
      }
      if (
        url.pathname === "/api/v1/agent/knowledge" &&
        req.method() === "POST"
      ) {
        const body = req.postDataJSON();
        article = {
          ...body,
          id,
          revision: 1,
          revision_id: revisionId,
          generation: 1,
          status: "DRAFT",
          index_state: "IDLE",
          retrieval_eligible: false,
          updated_at: now,
          created_at: now,
          revision_created_at: now,
          created_by: uuid(9002),
          editor_id: uuid(9002),
          changed_by: uuid(9002),
          index_model: null,
        };
        return send(article, 201);
      }
      if (!article) return send({}, 404);
      if (url.pathname.endsWith("/lifecycle")) {
        const body = req.postDataJSON();
        expect(body.expected_generation).toBe(article.generation);
        const published = body.operation === "PUBLISH";
        article = {
          ...article,
          generation: article.generation + 1,
          status: published ? "PUBLISHED" : "DRAFT",
          index_state: published ? "READY" : "IDLE",
          retrieval_eligible: published,
        };
        return send(article);
      }
      if (url.pathname.endsWith(id) && req.method() === "PATCH") {
        const body = req.postDataJSON();
        expect(body.expected_generation).toBe(article.generation);
        article = {
          ...article,
          body: body.body,
          status: "DRAFT",
          revision: 2,
          revision_id: uuid(7003),
          generation: article.generation + 1,
          index_state: "IDLE",
          retrieval_eligible: false,
        };
        return send(article);
      }
      if (url.pathname.endsWith("/revisions"))
        return send({ items: [], total: 0, page: 1, page_size: 20 });
      if (
        url.pathname.endsWith("/knowledge") ||
        url.pathname.endsWith("retrieval-preview")
      ) {
        return send({
          ticket_id: url.pathname.includes("/tickets/") ? uuid(1) : null,
          method: "hybrid",
          requested_method: "hybrid",
          semantic_state: "AVAILABLE",
          items: article.retrieval_eligible
            ? [
                {
                  article_id: article.id,
                  revision: article.revision,
                  revision_id: article.revision_id,
                  chunk_id: uuid(7010),
                  content_hash: "0".repeat(64),
                  title: article.title,
                  excerpt: article.body,
                  source_name: article.source_name,
                  source_type: "SYNTHETIC_TEST",
                  source_url: null,
                  method: "hybrid",
                  rank: 1,
                  score: 0.032,
                  score_type: "RECIPROCAL_RANK_FUSION",
                  lexical_rank: 1,
                  semantic_rank: 1,
                  embedding_model: "synthetic",
                },
              ]
            : [],
        });
      }
      return send(article);
    },
  );
  await signIn(page);
  await page.goto("/agent/knowledge");
  await expect(
    page.getByRole("heading", { name: "No matching articles" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "New article" }).click();
  await page
    .getByLabel("Title", { exact: true })
    .fill("Synthetic library note");
  await page
    .getByLabel("Article body")
    .fill("A fictional note for browser verification.");
  await page.getByLabel("Source type").selectOption("SYNTHETIC_TEST");
  await page.getByLabel("Source name").fill("Browser synthetic fixtures");
  await page
    .getByLabel("Reuse approval or license reference")
    .fill("Original test fixture");
  await page.getByRole("button", { name: "Save draft" }).press("Enter");
  await expect(
    page.getByRole("heading", { name: "Synthetic library note" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Publish article" }).click();
  await expect(page.getByText(/PUBLISHED · Revision 1/)).toBeVisible();
  await page.getByRole("button", { name: "Edit article" }).click();
  await page.getByLabel("Article body").fill("Revised fictional excerpt.");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText(/DRAFT · Revision 2/)).toBeVisible();
  await page.getByRole("button", { name: "Publish article" }).click();
  await expect(page.getByText(/PUBLISHED · Revision 2/)).toBeVisible();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.goto(`/agent/tickets/${uuid(1)}`);
  const evidence = page.getByRole("region", { name: "Relevant knowledge" });
  await evidence.getByRole("button", { name: "Find evidence" }).click();
  await expect(evidence.getByText("Revised fictional excerpt.")).toBeVisible();
  await expect(
    evidence.getByRole("link", { name: "Synthetic library note" }),
  ).toHaveAttribute("href", `/agent/knowledge/${id}?revision=2`);
  await page.goto(`/agent/knowledge/${id}`);
  await page.getByRole("button", { name: "Unpublish article" }).click();
  await expect(page.getByText(/DRAFT · Revision 2/)).toBeVisible();
  await page.goto(`/agent/tickets/${uuid(1)}`);
  await evidence.getByRole("button", { name: "Find evidence" }).click();
  await expect(
    evidence.getByText("No matching published evidence."),
  ).toBeVisible();
});

test("customer cannot mount the knowledge workspace", async ({ page }) => {
  const state = stateFor("CUSTOMER");
  await mockServices(page, state);
  await signIn(page);
  await page.goto("/agent/knowledge");
  await expect(page).toHaveURL(/\/customer$/);
  await expect(page.getByRole("link", { name: "Knowledge base" })).toHaveCount(
    0,
  );
  expect(
    state.requests.some((request) => request.path.includes("/agent/knowledge")),
  ).toBe(false);
});
