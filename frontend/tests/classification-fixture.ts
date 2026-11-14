import type {
  Classification,
  ClassificationRun,
  ClassificationState,
} from "../src/lib/api/types";
export const classificationTicket = "10000000-0000-4000-8000-000000000001";
export function classificationData(
  status: ClassificationState = "SUCCEEDED",
): Classification {
  const success = status === "SUCCEEDED";
  const terminal = ["SUCCEEDED", "FAILED", "UNAVAILABLE"].includes(status);
  const run: ClassificationRun = {
    id: "10000000-0000-4000-8000-000000007001",
    ticket_id: classificationTicket,
    generation: 1,
    status,
    model_version: "synthetic-m1",
    artifact_sha256: "a".repeat(64),
    artifact_verified: success,
    predicted_intent: success ? "FEATURE" : null,
    confidence: success ? 0.2 : null,
    score_type: "UNCALIBRATED_RAW_PROBABILITY",
    requires_human_review: true,
    automated_actions_allowed: false,
    low_score: success ? true : null,
    advisory_threshold: 0.5,
    threshold_validated: false,
    input_truncated: false,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: terminal ? "2026-01-01T00:00:01Z" : null,
    duration_ms: terminal ? 1 : null,
    failure_category:
      status === "UNAVAILABLE"
        ? "MODEL_MISSING"
        : status === "FAILED"
          ? "INFERENCE_FAILED"
          : null,
  };
  return {
    ticket_id: classificationTicket,
    status,
    current: status === "NOT_REQUESTED" ? null : run,
    recent_runs: status === "NOT_REQUESTED" ? [] : [run],
    total_runs: status === "NOT_REQUESTED" ? 0 : 1,
    supported_intents: [
      "ACCOUNT_ACCESS",
      "ACCOUNT_SECURITY",
      "BILLING_PAYMENT",
      "SUBSCRIPTION",
      "APP_TECHNICAL",
      "CONTENT",
      "FEATURE",
    ],
    unsupported_intents: ["UNKNOWN", "NON_SUPPORT", "OTHER_SUPPORT"],
    model_limitation:
      "Cannot detect UNKNOWN, NON_SUPPORT or OTHER_SUPPORT. APP_TECHNICAL recall was 0/4.",
    requires_human_review: true,
    automated_actions_allowed: false,
    retry_allowed: !["PENDING", "RUNNING"].includes(status),
    retry_available_at: null,
  };
}
