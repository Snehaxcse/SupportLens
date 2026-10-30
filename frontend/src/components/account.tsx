"use client";
import { useAuth } from "@/lib/auth";
import { PageHeading } from "./ui";
export function AccountScreen() {
  const { profile } = useAuth();
  if (!profile) return null;
  return (
    <>
      <PageHeading eyebrow="YOUR ACCOUNT" title="A familiar face.">
        Your profile is verified by Firebase. Workspace access comes from
        SupportLens.
      </PageHeading>
      <section className="card account-card">
        <h2>Profile details</h2>
        <dl className="profile-grid">
          <div>
            <dt>Display name</dt>
            <dd>{profile.display_name || "Not provided"}</dd>
          </div>
          <div>
            <dt>Email address</dt>
            <dd>{profile.email}</dd>
          </div>
          <div>
            <dt>Workspace role</dt>
            <dd>{profile.role === "AGENT" ? "Agent" : "Customer"}</dd>
          </div>
          <div>
            <dt>Account status</dt>
            <dd>{profile.is_active ? "Active" : "Inactive"}</dd>
          </div>
          <div>
            <dt>Joined</dt>
            <dd>
              {new Date(profile.created_at).toLocaleDateString(undefined, {
                dateStyle: "long",
              })}
            </dd>
          </div>
          <div>
            <dt>SupportLens ID</dt>
            <dd className="mono">{profile.id}</dd>
          </div>
        </dl>
        <p className="field-help">
          Profile editing and role changes are managed outside this portal.
          Contact the project administrator if your details need attention.
        </p>
      </section>
    </>
  );
}
