"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  LayoutDashboard,
  Inbox,
  CircleUserRound,
  LogOut,
  Menu,
  X,
  Plus,
  Headphones,
} from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { roleHome, useAuth } from "@/lib/auth";
import type { Role } from "@/lib/api/types";
import { authErrorMessage } from "@/lib/firebase";
import { Brand, Button, Loading, Notice } from "./ui";
import { SessionProblem } from "./auth-screens";
import { useHydrated } from "@/lib/hooks";

function subscribeOnline(callback: () => void) {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}
export function Workspace({
  role,
  children,
}: {
  role: Role;
  children: ReactNode;
}) {
  const auth = useAuth();
  const hydrated = useHydrated();
  const router = useRouter();
  useEffect(() => {
    if (!hydrated) return;
    if (auth.phase === "signed-out") router.replace("/sign-in");
    else if (auth.phase === "unverified") router.replace("/verify-email");
    else if (auth.phase === "ready" && auth.profile?.role !== role)
      router.replace(roleHome(auth.profile!.role));
  }, [hydrated, auth.phase, auth.profile, role, router]);
  if (hydrated && auth.phase === "error")
    return (
      <main id="main" className="standalone">
        <Brand />
        <h1 className="mt-8">Account access</h1>
        <div className="mt-8">
          <SessionProblem />
        </div>
      </main>
    );
  if (!hydrated || auth.phase !== "ready" || auth.profile?.role !== role)
    return (
      <main id="main">
        <Loading label="Checking workspace access…" />
      </main>
    );
  return <WorkspaceShell role={role}>{children}</WorkspaceShell>;
}
function WorkspaceShell({
  role,
  children,
}: {
  role: Role;
  children: ReactNode;
}) {
  const auth = useAuth();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [busy, setBusy] = useState(false);
  const online = useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
  const base = roleHome(role);
  const isAgent = role === "AGENT";
  const navigation = (
    <>
      <Brand href={base} />
      <div className="workspace-label">
        {isAgent ? "AGENT WORKSPACE" : "CUSTOMER PORTAL"}
      </div>
      <nav aria-label="Workspace navigation">
        {[
          { href: base, label: "Overview", icon: LayoutDashboard },
          {
            href: base + "/tickets",
            label: isAgent ? "Ticket inbox" : "My tickets",
            icon: Inbox,
          },
          {
            href: base + "/account",
            label: "My account",
            icon: CircleUserRound,
          },
        ].map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            onClick={() => setOpen(false)}
            className={`nav-item ${path === href || (href.endsWith("/tickets") && path.startsWith(href + "/")) ? "active" : ""}`}
            aria-current={path === href ? "page" : undefined}
            href={href}
          >
            <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
            {label}
          </Link>
        ))}
      </nav>
      {!isAgent && (
        <Link
          onClick={() => setOpen(false)}
          className="button button-primary sidebar-create"
          href="/customer/tickets/new"
        >
          <Plus size={17} aria-hidden="true" />
          New request
        </Link>
      )}
      <div className="sidebar-bottom">
        <div className="sidebar-tip">
          <Headphones size={22} strokeWidth={1.5} aria-hidden="true" />
          <strong>A little context helps.</strong>
          <p>
            {isAgent
              ? "Keep public replies clear. Keep investigation details in internal notes."
              : "Add device details and what you’ve tried. Never share passwords or payment details."}
          </p>
        </div>
        <div className="sidebar-user">
          <span className="avatar">
            {(auth.profile?.display_name || (isAgent ? "Agent" : "You"))
              .slice(0, 1)
              .toUpperCase()}
          </span>
          <div>
            <strong>
              {auth.profile?.display_name ||
                (isAgent ? "Support agent" : "Your account")}
            </strong>
            <span>{isAgent ? "Agent" : "Customer"}</span>
          </div>
        </div>
        <Button
          variant="quiet"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await auth.signOut();
            } catch (error) {
              setLogoutError(authErrorMessage(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <LogOut size={16} aria-hidden="true" />
          {busy ? "Signing out…" : "Sign out"}
        </Button>
      </div>
    </>
  );
  return (
    <div className="workspace">
      <aside className="sidebar">{navigation}</aside>
      <div className="workspace-body">
        <header className="workspace-topbar">
          <div className="mobile-navigation">
            <Dialog.Root open={open} onOpenChange={setOpen}>
              <Dialog.Trigger asChild>
                <Button variant="quiet" aria-label="Open navigation">
                  <Menu size={21} />
                </Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="dialog-overlay" />
                <Dialog.Content className="mobile-sidebar">
                  <Dialog.Title className="sr-only">
                    Main navigation
                  </Dialog.Title>
                  <Dialog.Description className="sr-only">
                    Choose a page in your authorized workspace.
                  </Dialog.Description>
                  <Dialog.Close asChild>
                    <Button
                      className="mobile-nav-close"
                      variant="quiet"
                      aria-label="Close navigation"
                    >
                      <X size={20} />
                    </Button>
                  </Dialog.Close>
                  {navigation}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </div>
          <span className="topbar-context">
            {isAgent ? "Workspace" : "Your support space"}
            <span aria-hidden="true">/</span>
            {path.includes("/tickets")
              ? "Tickets"
              : path.includes("/account")
                ? "Account"
                : "Overview"}
          </span>
          <span className="role-label">{isAgent ? "Agent" : "Customer"}</span>
        </header>
        <main id="main" className="workspace-main">
          {!online && (
            <Notice title="You’re offline." kind="error">
              Check your connection before submitting a change. Refresh data
              once you reconnect.
            </Notice>
          )}
          {logoutError && <Notice title={logoutError} kind="error" />}
          {auth.announcement && (
            <Notice
              kind="success"
              title={auth.announcement}
              action={
                <Button variant="quiet" onClick={() => auth.announce("")}>
                  Dismiss
                </Button>
              }
            />
          )}
          {children}
        </main>
        <footer className="workspace-footer">
          SupportLens{" "}
          <span>Independent support · Not affiliated with Spotify</span>
          <span>Dates use your local time</span>
        </footer>
      </div>
    </div>
  );
}
