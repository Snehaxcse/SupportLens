import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button, ConfirmAction, Empty, Loading, Notice } from "@/components/ui";

describe("accessible foundation", () => {
  it("announces loading and errors", () => {
    render(
      <>
        <Loading />
        <Notice title="Connection unavailable" kind="error" />
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Connection unavailable",
    );
  });
  it("uses a disabled native control", async () => {
    const click = vi.fn();
    render(
      <Button disabled onClick={click}>
        Save
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(click).not.toHaveBeenCalled();
  });
  it("provides a useful empty state", () => {
    render(<Empty title="No requests yet">Start with a question.</Empty>);
    expect(
      screen.getByRole("heading", { name: "No requests yet" }),
    ).toBeVisible();
  });
  it("requires confirmation and returns focus on cancel", async () => {
    const confirm = vi.fn().mockResolvedValue(true);
    render(
      <ConfirmAction
        label="Resolve ticket"
        title="Ready to resolve?"
        onConfirm={confirm}
      >
        The customer can reopen it.
      </ConfirmAction>,
    );
    const trigger = screen.getByRole("button", { name: "Resolve ticket" });
    await userEvent.click(trigger);
    expect(screen.getByRole("alertdialog")).toBeVisible();
    expect(confirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
