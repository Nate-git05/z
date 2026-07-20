"use client";

import { FormEvent, useEffect, useState } from "react";
import { postJson } from "@/lib/api";

type Props = {
  open: boolean;
  interestTag?: string;
  onClose: () => void;
};

export function WaitlistModal({ open, interestTag = "", onClose }: Props) {
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      setError("");
      setSuccess(false);
      setSubmitting(false);
    }
  }, [open]);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    const form = e.currentTarget;
    const first = (form.first_name as HTMLInputElement).value.trim();
    const last = (form.last_name as HTMLInputElement).value.trim();
    const email = (form.email as HTMLInputElement).value.trim();
    if (!first || !last || !email) {
      setError("Please fill in all fields.");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setError("Enter a valid email address.");
      return;
    }
    setSubmitting(true);
    const result = await postJson<{
      ok?: boolean;
      detail?: string;
      message?: string;
    }>("/v1/waitlist", {
      first_name: first,
      last_name: last,
      email,
      interest: interestTag || null,
    });
    if (result.ok && result.data?.ok) {
      setSuccess(true);
      setSubmitting(false);
      return;
    }
    const msg =
      (typeof result.data?.detail === "string" && result.data.detail) ||
      (typeof result.data?.message === "string" && result.data.message) ||
      "Something went wrong — try again";
    setError(msg);
    setSubmitting(false);
  }

  if (!open) return null;

  return (
    <div
      id="waitlist-modal"
      className="lp-modal lp-modal-open"
      aria-hidden="false"
    >
      <div className="lp-modal-backdrop" onClick={onClose} />
      <div
        className="lp-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="waitlist-modal-title"
      >
        <button
          type="button"
          className="lp-modal-close"
          onClick={onClose}
          aria-label="Close"
        >
          ×
        </button>
        <p className="lp-label">Waitlist</p>
        <h2 id="waitlist-modal-title">Join the waitlist</h2>
        <p>
          Want updates on workspace sharing, cost-optimized routing across model
          providers, MCP integrations, and what&apos;s next? Join the list — you
          can still try early testing right now.
        </p>
        {error ? (
          <div className="waitlist-error visible" role="alert">
            {error}
          </div>
        ) : null}
        {!success ? (
          <form className="waitlist-form" onSubmit={onSubmit} noValidate>
            <div className="row-2">
              <div>
                <label htmlFor="first_name">First name</label>
                <input
                  id="first_name"
                  name="first_name"
                  type="text"
                  autoComplete="given-name"
                  required
                  placeholder="Ada"
                />
              </div>
              <div>
                <label htmlFor="last_name">Last name</label>
                <input
                  id="last_name"
                  name="last_name"
                  type="text"
                  autoComplete="family-name"
                  required
                  placeholder="Lovelace"
                />
              </div>
            </div>
            <div>
              <label htmlFor="email">Email</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                placeholder="ada@example.com"
              />
            </div>
            <button className="lp-btn lp-btn-block" type="submit" disabled={submitting}>
              {submitting ? "Joining…" : "Join waitlist"}
            </button>
          </form>
        ) : (
          <div className="waitlist-success visible" aria-live="polite">
            <div className="check" aria-hidden="true">
              ✓
            </div>
            <h3>You&apos;re on the list</h3>
            <p>We&apos;ll be in touch when a spot opens. No spam.</p>
          </div>
        )}
      </div>
    </div>
  );
}
