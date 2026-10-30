import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
const mocked = vi.hoisted(() => ({
  replace: vi.fn(),
  state: {
    phase: "ready",
    profile: {
      id: "synthetic-customer",
      display_name: "Synthetic Customer",
      role: "CUSTOMER",
    },
    announcement: "",
    error: null,
    user: null,
    refresh: vi.fn(),
    signOut: vi.fn(),
    announce: vi.fn(),
  },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocked.replace }),
  usePathname: () => "/agent/account",
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => mocked.state,
  roleHome: (role: string) => (role === "AGENT" ? "/agent" : "/customer"),
}));
import { Workspace } from "@/components/shell";
beforeEach(() => {
  mocked.replace.mockClear();
  mocked.state.phase = "ready";
  mocked.state.profile.role = "CUSTOMER";
});
describe("workspace access boundaries", () => {
  it("never mounts agent content for a customer", async () => {
    render(
      <Workspace role="AGENT">
        <div>PRIVATE_AGENT_CONTENT</div>
      </Workspace>,
    );
    expect(screen.queryByText("PRIVATE_AGENT_CONTENT")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(mocked.replace).toHaveBeenCalledWith("/customer"),
    );
  });
  it("never mounts customer content for an agent", async () => {
    mocked.state.profile.role = "AGENT";
    render(
      <Workspace role="CUSTOMER">
        <div>PRIVATE_CUSTOMER_CONTENT</div>
      </Workspace>,
    );
    expect(
      screen.queryByText("PRIVATE_CUSTOMER_CONTENT"),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(mocked.replace).toHaveBeenCalledWith("/agent"));
  });
  it.each([
    ["signed-out", "/sign-in"],
    ["unverified", "/verify-email"],
  ])(
    "redirects %s before mounting operational components",
    async (phase, path) => {
      mocked.state.phase = phase;
      render(
        <Workspace role="CUSTOMER">
          <div>OPERATIONAL_CONTENT</div>
        </Workspace>,
      );
      expect(screen.queryByText("OPERATIONAL_CONTENT")).not.toBeInTheDocument();
      await waitFor(() => expect(mocked.replace).toHaveBeenCalledWith(path));
    },
  );
  it("mounts an authorized customer workspace", () => {
    render(
      <Workspace role="CUSTOMER">
        <div>Authorized content</div>
      </Workspace>,
    );
    expect(screen.getByText("Authorized content")).toBeVisible();
  });
  it("clears the operational screen for a stale session", () => {
    mocked.state.phase = "error";
    render(
      <Workspace role="CUSTOMER">
        <div>OPERATIONAL_CONTENT</div>
      </Workspace>,
    );
    expect(screen.queryByText("OPERATIONAL_CONTENT")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeVisible();
  });
});
