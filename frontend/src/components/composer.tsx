"use client";
import { useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { LockKeyhole, Send } from "lucide-react";
import type { Visibility } from "@/lib/api/types";
import { validateText } from "@/lib/tickets";
import { Button } from "./ui";

export function Composer({
  agent = false,
  resolved,
  disabled = false,
  onSend,
}: {
  agent?: boolean;
  resolved: boolean;
  disabled?: boolean;
  onSend: (body: string, visibility: Visibility) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<Visibility>("PUBLIC");
  const [drafts, setDrafts] = useState({ PUBLIC: "", INTERNAL: "" });
  const [error, setError] = useState("");
  const current = agent ? mode : "PUBLIC";
  const blocked = disabled || (resolved && current === "PUBLIC");
  return (
    <section
      className={`composer ${current === "INTERNAL" ? "composer-internal" : ""}`}
      aria-label="Message composer"
    >
      <Tabs.Root
        value={current}
        onValueChange={(value) => {
          setMode(value as Visibility);
          setError("");
        }}
      >
        <Tabs.List className="composer-tabs" aria-label="Message visibility">
          <Tabs.Trigger value="PUBLIC" disabled={disabled}>
            Public reply
          </Tabs.Trigger>
          {agent && (
            <Tabs.Trigger value="INTERNAL" disabled={disabled}>
              <LockKeyhole size={13} aria-hidden="true" />
              Internal note
            </Tabs.Trigger>
          )}
        </Tabs.List>
        {(["PUBLIC", ...(agent ? ["INTERNAL"] : [])] as Visibility[]).map(
          (visibility) => (
            <Tabs.Content value={visibility} key={visibility}>
              <form
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (blocked) return;
                  const body = drafts[visibility];
                  const validation = validateText(body, 20000);
                  setError(validation);
                  if (validation) return;
                  if (await onSend(body.trim(), visibility))
                    setDrafts((previous) => ({
                      ...previous,
                      [visibility]: "",
                    }));
                }}
              >
                <div className="composer-mode-description">
                  {visibility === "INTERNAL" ? (
                    <>
                      <LockKeyhole size={13} aria-hidden="true" />
                      Private to agents. This note will never be sent as a
                      public reply.
                    </>
                  ) : agent ? (
                    "Visible to the customer. Only send information you intend to share."
                  ) : (
                    "Visible to you and the support agents working on this request."
                  )}
                </div>
                <div className="field">
                  <label className="sr-only" htmlFor={`message-${visibility}`}>
                    {visibility === "INTERNAL"
                      ? "Internal note"
                      : "Public reply"}
                  </label>
                  <textarea
                    id={`message-${visibility}`}
                    value={drafts[visibility]}
                    onChange={(event) =>
                      setDrafts((previous) => ({
                        ...previous,
                        [visibility]: event.target.value,
                      }))
                    }
                    maxLength={20000}
                    disabled={disabled}
                    readOnly={resolved && visibility === "PUBLIC"}
                    aria-invalid={!!error}
                    aria-describedby={`composer-help-${visibility}${error ? " composer-error-" + visibility : ""}`}
                    placeholder={
                      visibility === "INTERNAL"
                        ? "Add investigation details for agents…"
                        : "Add a reply or a little more context…"
                    }
                  />
                  {error && (
                    <p
                      id={`composer-error-${visibility}`}
                      className="field-error"
                      role="alert"
                    >
                      {error}
                    </p>
                  )}
                  <p id={`composer-help-${visibility}`} className="field-help">
                    {resolved && visibility === "PUBLIC"
                      ? "This ticket is resolved. Reopen it before replying."
                      : "Plain text only. Up to 20,000 characters. Never include passwords or payment details."}
                  </p>
                </div>
                <div className="composer-actions">
                  <span>
                    {drafts[visibility].length.toLocaleString()} / 20,000
                  </span>
                  <Button
                    type="submit"
                    disabled={blocked || !drafts[visibility].trim()}
                  >
                    <Send size={14} aria-hidden="true" />
                    {disabled
                      ? "Saving…"
                      : visibility === "INTERNAL"
                        ? "Save internal note"
                        : "Send reply"}
                  </Button>
                </div>
              </form>
            </Tabs.Content>
          ),
        )}
      </Tabs.Root>
    </section>
  );
}
