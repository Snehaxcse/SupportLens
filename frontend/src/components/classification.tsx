"use client";
import { useCallback, useEffect } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import { date, title } from "@/lib/tickets";
import type { ClassificationState } from "@/lib/api/types";
import { Button, Loading, Notice } from "./ui";

const labels: Record<ClassificationState, string> = {
  NOT_REQUESTED: "Not yet classified",
  PENDING: "Queued",
  RUNNING: "Processing",
  SUCCEEDED: "Suggestion ready",
  FAILED: "Classification failed",
  UNAVAILABLE: "Classifier unavailable",
};
const failures: Record<string, string> = {
  MODEL_MISSING: "The reviewed model is not available to this backend.",
  ARTIFACT_INVALID: "Model integrity or compatibility checks did not pass.",
  RUNTIME_INCOMPATIBLE:
    "The classifier runtime does not match the reviewed model.",
  MODEL_DISABLED: "Classification is currently disabled.",
  INVALID_INPUT: "This ticket could not be prepared for the classifier.",
  INFERENCE_FAILED: "The classifier could not produce a valid suggestion.",
  UNSUPPORTED_OUTPUT:
    "The classifier returned an unsupported output. No prediction was accepted.",
  RECOVERY_EXHAUSTED: "Interrupted processing exhausted its recovery attempts.",
};

export function ClassificationPanel({ ticketId }: { ticketId: string }) {
  const { api, announce } = useAuth();
  const load = useCallback(
    (signal: AbortSignal) => api.classification(ticketId, signal),
    [api, ticketId],
  );
  const resource = useResource(`classification:${ticketId}`, load);
  const mutation = useMutation();
  const data = resource.data;
  const current = data?.current;
  const active = data?.status === "PENDING" || data?.status === "RUNNING";
  useEffect(() => {
    if (!data || resource.loading || resource.error) return;
    const delay = active
      ? 10000
      : !data.retry_allowed && data.retry_available_at
        ? Math.max(
            1000,
            Math.min(
              60000,
              Date.parse(data.retry_available_at) - Date.now() + 200,
            ),
          )
        : null;
    if (delay === null) return;
    const timer = window.setTimeout(resource.reload, delay);
    return () => window.clearTimeout(timer);
  }, [data, active, resource.loading, resource.error, resource.reload]);
  return (
    <section
      className="card classification-card"
      aria-labelledby="classification-title"
    >
      <div className="classification-heading">
        <h2 id="classification-title">ML suggestion</h2>
        <span className="classification-review">Human review required</span>
      </div>
      <p className="field-help">
        Advisory intent classification. Ticket actions remain under agent
        control.
      </p>
      {resource.loading && !data && (
        <Loading label="Loading the ML suggestion…" />
      )}
      {(resource.error || mutation.error) && (
        <Notice kind="error" title="ML suggestion needs attention">
          {(mutation.error || resource.error)?.message}
        </Notice>
      )}
      {data && (
        <>
          <p className="classification-state" role="status">
            {labels[data.status]}
          </p>
          {current?.status === "SUCCEEDED" && current.predicted_intent && (
            <>
              <p className="classification-intent">
                {title(current.predicted_intent)}
              </p>
              <dl className="classification-facts">
                <div>
                  <dt>Model score</dt>
                  <dd>{((current.confidence || 0) * 100).toFixed(1)}%</dd>
                </div>
                <div>
                  <dt>Inference time</dt>
                  <dd>{current.duration_ms?.toLocaleString()} ms</dd>
                </div>
              </dl>
              <p className="field-help">
                Uncalibrated raw probability. This score is not an accuracy
                estimate.
              </p>
              {current.low_score && (
                <p className="classification-warning">
                  Low advisory score: below the configured{" "}
                  {(current.advisory_threshold * 100).toLocaleString()}% flag.
                  This threshold has not been validated.
                </p>
              )}
            </>
          )}
          {current?.failure_category && (
            <p className="classification-warning">
              {failures[current.failure_category]}
            </p>
          )}
          {active && (
            <p className="field-help">
              Processing is saved and can resume after a restart. You can
              continue working on the ticket.
            </p>
          )}
          {current?.input_truncated && (
            <p className="classification-warning">
              Only the first 10,000 characters of the combined subject and
              description were classified. The original ticket is preserved.
            </p>
          )}
          <details className="classification-details">
            <summary>Model details and limitations</summary>
            <p className="field-help">{data.model_limitation}</p>
            {current && (
              <dl className="classification-provenance">
                <div>
                  <dt>Model version</dt>
                  <dd>{current.model_version}</dd>
                </div>
                <div>
                  <dt>
                    {current.artifact_verified
                      ? "Verified artifact SHA-256"
                      : "Expected artifact SHA-256"}
                  </dt>
                  <dd className="mono">{current.artifact_sha256}</dd>
                </div>
                <div>
                  <dt>Run</dt>
                  <dd>Generation {current.generation}</dd>
                </div>
                {current.completed_at && (
                  <div>
                    <dt>Completed</dt>
                    <dd>{date(current.completed_at)}</dd>
                  </div>
                )}
              </dl>
            )}
            <p className="field-help">
              {data.total_runs} saved{" "}
              {data.total_runs === 1 ? "attempt" : "attempts"}. Earlier runs are
              retained. Automated actions are disabled.
            </p>
          </details>
          {!data.retry_allowed && !active && data.retry_available_at && (
            <p className="field-help">
              Another run is available after {date(data.retry_available_at)}.
              Availability refreshes automatically.
            </p>
          )}
          <Button
            variant="secondary"
            disabled={
              !data.retry_allowed ||
              active ||
              resource.loading ||
              mutation.busy ||
              !!resource.error
            }
            onClick={async () => {
              const result = await mutation.run(() =>
                api.retryClassification(ticketId, {
                  expected_run_id: current?.id ?? null,
                }),
              );
              if (result) {
                announce(
                  "Classification request acknowledged. Refreshing the current suggestion.",
                );
                resource.reload();
              }
            }}
          >
            {mutation.busy
              ? "Requesting…"
              : current
                ? "Retry classification"
                : "Classify ticket"}
          </Button>
        </>
      )}
      <Button
        variant="quiet"
        disabled={resource.loading || mutation.busy}
        onClick={() => {
          mutation.clearError();
          resource.reload();
        }}
      >
        <RefreshCw size={14} aria-hidden="true" /> Refresh suggestion
      </Button>
    </section>
  );
}
