"use client";

import Link from "next/link";
import { ArrowRight, CircleHelp, LoaderCircle, SearchX } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { useState } from "react";

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link className="brand" href={href} aria-label="SupportLens home">
      <span className="brand-mark" aria-hidden="true">
        <span />
      </span>
      SupportLens<span className="brand-dot">.</span>
    </Link>
  );
}
export function Button({
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
}) {
  return (
    <button className={`button button-${variant} ${className}`} {...props}>
      {children}
    </button>
  );
}
export function ButtonLink({
  href,
  children,
  secondary = false,
}: {
  href: string;
  children: ReactNode;
  secondary?: boolean;
}) {
  return (
    <Link
      className={`button button-${secondary ? "secondary" : "primary"}`}
      href={href}
    >
      {children}
    </Link>
  );
}
export function Loading({
  label = "Loading your workspace…",
}: {
  label?: string;
}) {
  return (
    <div className="loading-state" role="status">
      <LoaderCircle className="spin" size={22} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
export function Notice({
  children,
  title,
  kind = "info",
  action,
}: {
  children?: ReactNode;
  title: string;
  kind?: "info" | "error" | "success";
  action?: ReactNode;
}) {
  return (
    <div
      className={`notice notice-${kind}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <CircleHelp size={19} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {children && <div className="notice-description">{children}</div>}
        {action && <div className="notice-action">{action}</div>}
      </div>
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <SearchX size={26} aria-hidden="true" />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {children && <p className="page-description">{children}</p>}
      </div>
      {action && <div className="heading-actions">{action}</div>}
    </header>
  );
}
export function TextLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <Link className="text-link" href={href}>
      {children}
      <ArrowRight size={16} aria-hidden="true" />
    </Link>
  );
}
export function ConfirmAction({
  label,
  title,
  children,
  onConfirm,
  disabled = false,
}: {
  label: string;
  title: string;
  children: ReactNode;
  onConfirm: () => Promise<boolean>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!pending) {
          setOpen(value);
          setFailed(false);
        }
      }}
    >
      <AlertDialog.Trigger asChild>
        <Button variant="secondary" disabled={disabled}>
          {label}
        </Button>
      </AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="dialog-overlay" />
        <AlertDialog.Content className="dialog-content">
          <AlertDialog.Title asChild>
            <h2>{title}</h2>
          </AlertDialog.Title>
          <AlertDialog.Description asChild>
            <p>{children}</p>
          </AlertDialog.Description>
          {failed && (
            <p role="alert">
              The change was not confirmed. Close this dialog and review the
              error.
            </p>
          )}
          <div className="dialog-actions">
            <AlertDialog.Cancel asChild>
              <Button variant="quiet" disabled={pending}>
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button
                disabled={pending}
                onClick={async (event) => {
                  event.preventDefault();
                  setPending(true);
                  try {
                    const ok = await onConfirm();
                    if (ok) setOpen(false);
                    else setFailed(true);
                  } finally {
                    setPending(false);
                  }
                }}
              >
                {pending ? "Saving…" : label}
              </Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
