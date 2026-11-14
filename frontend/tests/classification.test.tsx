import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import {
  classificationData,
  classificationTicket,
} from "./classification-fixture";
const mocked = vi.hoisted(() => ({
  data: undefined as unknown,
  api: { classification: vi.fn(), retryClassification: vi.fn() },
  announce: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ api: mocked.api, announce: mocked.announce }),
}));
import { ClassificationPanel } from "@/components/classification";
beforeEach(() => {
  mocked.data = classificationData();
  mocked.api.classification
    .mockReset()
    .mockImplementation(async () => mocked.data);
  mocked.api.retryClassification.mockReset();
  mocked.announce.mockClear();
});
describe("advisory classification panel", () => {
  it("keeps high scores advisory and requires review", async () => {
    const data = classificationData();
    data.current!.confidence = 0.95;
    data.current!.low_score = false;
    mocked.data = data;
    render(<ClassificationPanel ticketId={classificationTicket} />);
    await screen.findByText("95.0%");
    expect(screen.getByText("Human review required")).toBeVisible();
    expect(screen.queryByText(/Low advisory score/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /apply|resolve|assign/i }),
    ).not.toBeInTheDocument();
  });
  it("shows a raw score, mandatory review and an unvalidated low-score flag", async () => {
    render(<ClassificationPanel ticketId={classificationTicket} />);
    await screen.findByText("Suggestion ready");
    expect(screen.getByText("20.0%")).toBeVisible();
    expect(screen.getByText("Human review required")).toBeVisible();
    expect(screen.getByText(/Low advisory score/)).toHaveTextContent(
      "has not been validated",
    );
    fireEvent.click(screen.getByText("Model details and limitations"));
    expect(screen.getByText(/Cannot detect UNKNOWN/)).toHaveTextContent(
      "APP_TECHNICAL recall was 0/4",
    );
    expect(screen.getByText(/Automated actions are disabled/)).toBeVisible();
    expect(screen.getByText("synthetic-m1")).toBeVisible();
  });
  it.each(["PENDING", "RUNNING"] as const)(
    "keeps retry disabled during %s",
    async (status) => {
      mocked.data = classificationData(status);
      render(<ClassificationPanel ticketId={classificationTicket} />);
      await screen.findByText(status === "PENDING" ? "Queued" : "Processing");
      expect(
        screen.getByRole("button", { name: "Retry classification" }),
      ).toBeDisabled();
      expect(screen.queryByText("20.0%")).not.toBeInTheDocument();
    },
  );
  it.each(["FAILED", "UNAVAILABLE"] as const)(
    "distinguishes %s from a low score",
    async (status) => {
      mocked.data = classificationData(status);
      render(<ClassificationPanel ticketId={classificationTicket} />);
      await screen.findByText(
        status === "FAILED"
          ? "Classification failed"
          : "Classifier unavailable",
      );
      expect(screen.queryByText(/Low advisory score/)).not.toBeInTheDocument();
      expect(screen.queryByText("Model score")).not.toBeInTheDocument();
    },
  );
  it("marks unsupported output without displaying a reliable prediction", async () => {
    const data = classificationData("FAILED");
    data.current!.failure_category = "UNSUPPORTED_OUTPUT";
    mocked.data = data;
    render(<ClassificationPanel ticketId={classificationTicket} />);
    await screen.findByText(/returned an unsupported output/);
    expect(
      screen.queryByText("Feature", { exact: true }),
    ).not.toBeInTheDocument();
  });
  it("classifies a legacy ticket with an explicit null predecessor", async () => {
    mocked.data = classificationData("NOT_REQUESTED");
    mocked.api.retryClassification.mockImplementation(async () => {
      mocked.data = classificationData("PENDING");
      return (mocked.data as ReturnType<typeof classificationData>).current;
    });
    render(<ClassificationPanel ticketId={classificationTicket} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Classify ticket" }),
    );
    await screen.findByText("Queued");
    expect(mocked.api.retryClassification).toHaveBeenCalledWith(
      classificationTicket,
      { expected_run_id: null },
    );
  });
  it("submits the observed run once and reloads after acknowledgment", async () => {
    const data = classificationData("FAILED");
    mocked.data = data;
    mocked.api.retryClassification.mockImplementation(async () => {
      mocked.data = classificationData("PENDING");
      return (mocked.data as ReturnType<typeof classificationData>).current;
    });
    render(<ClassificationPanel ticketId={classificationTicket} />);
    const button = await screen.findByRole("button", {
      name: "Retry classification",
    });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText("Queued");
    expect(mocked.api.retryClassification).toHaveBeenCalledTimes(1);
    expect(mocked.api.retryClassification).toHaveBeenCalledWith(
      classificationTicket,
      { expected_run_id: data.current!.id },
    );
  });
  it("reports explicit input shortening while preserving ticket tools", async () => {
    const data = classificationData();
    data.current!.input_truncated = true;
    mocked.data = data;
    render(<ClassificationPanel ticketId={classificationTicket} />);
    await screen.findByText(/Only the first 10,000 characters/);
  });
  it("handles classification errors locally without displaying raw errors", async () => {
    mocked.api.classification.mockRejectedValue(
      new Error("PRIVATE_MODEL_TRACE"),
    );
    render(<ClassificationPanel ticketId={classificationTicket} />);
    await screen.findByRole("alert");
    expect(screen.queryByText("PRIVATE_MODEL_TRACE")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Refresh suggestion/ }),
    ).toBeEnabled();
  });
  it("cleans up pending polling on unmount", async () => {
    vi.useFakeTimers();
    try {
      mocked.data = classificationData("PENDING");
      let mounted: ReturnType<typeof render>;
      await act(async () => {
        mounted = render(
          <ClassificationPanel ticketId={classificationTicket} />,
        );
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(mocked.api.classification).toHaveBeenCalledTimes(2);
      mounted!.unmount();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20000);
      });
      expect(mocked.api.classification).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
