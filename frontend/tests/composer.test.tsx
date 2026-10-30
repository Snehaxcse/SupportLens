import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "@/components/composer";
import { Conversation } from "@/components/ticket-ui";
describe("public and internal message boundaries", () => {
  it("keeps independent drafts and submits the explicit selected visibility", async () => {
    const send = vi.fn().mockResolvedValue(true);
    render(<Composer agent resolved={false} onSend={send} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Public reply" }), {
      target: { value: "Synthetic public draft" },
    });
    await userEvent.click(screen.getByRole("tab", { name: "Internal note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Internal note" }), {
      target: { value: "Synthetic private investigation" },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Save internal note" }),
    );
    expect(send).toHaveBeenCalledWith(
      "Synthetic private investigation",
      "INTERNAL",
    );
    expect(screen.getByRole("tab", { name: "Internal note" })).toHaveAttribute(
      "data-state",
      "active",
    );
    await userEvent.click(screen.getByRole("tab", { name: "Public reply" }));
    expect(screen.getByRole("textbox", { name: "Public reply" })).toHaveValue(
      "Synthetic public draft",
    );
    expect(send).toHaveBeenCalledTimes(1);
  });
  it("does not clear the draft or show success after a failed submission", async () => {
    render(
      <Composer resolved={false} onSend={vi.fn().mockResolvedValue(false)} />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Public reply" }), {
      target: { value: "Keep this synthetic draft" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
    expect(screen.getByRole("textbox", { name: "Public reply" })).toHaveValue(
      "Keep this synthetic draft",
    );
  });
  it("blocks resolved public replies while allowing an internal note", async () => {
    render(<Composer agent resolved onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Send reply" })).toBeDisabled();
    expect(
      screen.getByRole("textbox", { name: "Public reply" }),
    ).toHaveAttribute("readonly");
    await userEvent.click(screen.getByRole("tab", { name: "Internal note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Internal note" }), {
      target: { value: "Synthetic note on a resolved ticket" },
    });
    expect(
      screen.getByRole("button", { name: "Save internal note" }),
    ).toBeEnabled();
  });
  it("does not offer internal notes to customers", () => {
    render(<Composer resolved={false} onSend={vi.fn()} />);
    expect(
      screen.queryByRole("tab", { name: "Internal note" }),
    ).not.toBeInTheDocument();
  });
  it("renders untrusted message text as text, without executing HTML", () => {
    render(
      <Conversation
        messages={[
          {
            id: "synthetic",
            body: "<img src=x onerror=alert(1)>",
            visibility: "PUBLIC",
            author: { display_name: "Synthetic", kind: "CUSTOMER" },
            created_at: "2026-01-01T00:00:00Z",
          },
        ]}
      />,
    );
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
  });
  it("refuses to render a leaked internal DTO in a customer conversation", () => {
    render(
      <Conversation
        messages={[
          {
            id: "synthetic",
            body: "PRIVATE_SYNTHETIC_NOTE",
            visibility: "INTERNAL",
            author_id: "synthetic-agent",
            author: { display_name: "Synthetic Agent", kind: "AGENT" },
            created_at: "2026-01-01T00:00:00Z",
          },
        ]}
      />,
    );
    expect(
      screen.queryByText("PRIVATE_SYNTHETIC_NOTE"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeVisible();
  });
});
