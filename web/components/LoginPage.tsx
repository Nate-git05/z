"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { SiteShell } from "@/components/SiteShell";
import { postJson } from "@/lib/api";
import { saveSession, type ZSession } from "@/lib/auth";

type Step = "start" | "verify";

export function LoginPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("start");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("000000");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");

  async function onStart(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const result = await postJson<{
      method?: string;
      session_id?: string;
      detail?: string;
    }>("/v1/auth/email/start", {
      email: email.trim(),
      name: name.trim() || null,
      method: "otp",
    });
    setBusy(false);
    if (!result.ok) {
      setError(
        typeof result.data?.detail === "string"
          ? result.data.detail
          : "Could not send a code. Try again."
      );
      return;
    }
    setInfo(
      "Code sent. In local/dev mode, use 000000 if email delivery isn’t configured."
    );
    setStep("verify");
  }

  async function onVerify(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const result = await postJson<ZSession & { detail?: string }>(
      "/v1/auth/email/verify",
      {
        email: email.trim(),
        code: code.trim(),
        name: name.trim() || null,
      }
    );
    setBusy(false);
    if (!result.ok || !result.data?.access_token) {
      setError(
        typeof result.data?.detail === "string"
          ? result.data.detail
          : "Invalid or expired code."
      );
      return;
    }
    saveSession(result.data);
    router.push("/");
    router.refresh();
  }

  return (
    <SiteShell>
      <main className="pricing-page" style={{ maxWidth: 520 }}>
        <p className="lp-label">Account</p>
        <h1 style={{ fontSize: "clamp(1.8rem, 3vw, 2.4rem)" }}>
          {step === "start" ? "Sign in / sign up" : "Enter your code"}
        </h1>
        <p className="pricing-intro">
          Same Z account as the CLI. Email OTP — Google and phone are available
          from the terminal today; web Google OAuth lands next.
        </p>

        {error ? (
          <div className="waitlist-error visible" role="alert">
            {error}
          </div>
        ) : null}
        {info ? (
          <p style={{ color: "var(--muted)", marginTop: 0 }}>{info}</p>
        ) : null}

        {step === "start" ? (
          <form className="waitlist-form" onSubmit={onStart}>
            <div>
              <label htmlFor="login-email">Email</label>
              <input
                id="login-email"
                type="email"
                required
                autoComplete="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="login-name">Name (optional)</label>
              <input
                id="login-name"
                type="text"
                autoComplete="name"
                placeholder="Ada"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <button className="lp-btn lp-btn-block" type="submit" disabled={busy}>
              {busy ? "Sending…" : "Continue with email"}
            </button>
          </form>
        ) : (
          <form className="waitlist-form" onSubmit={onVerify}>
            <div>
              <label htmlFor="login-code">One-time code</label>
              <input
                id="login-code"
                type="text"
                inputMode="numeric"
                required
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
            <button className="lp-btn lp-btn-block" type="submit" disabled={busy}>
              {busy ? "Verifying…" : "Verify and continue"}
            </button>
            <button
              type="button"
              className="lp-btn lp-btn-outline lp-btn-block"
              onClick={() => {
                setStep("start");
                setInfo("");
                setError("");
              }}
            >
              Use a different email
            </button>
          </form>
        )}

        <p style={{ marginTop: "2rem", color: "var(--muted)", fontSize: "0.95rem" }}>
          Prefer the terminal? Run <code className="mono">z login</code>.{" "}
          <Link href="/" style={{ color: "var(--accent)" }}>
            Back to home
          </Link>
        </p>
      </main>
    </SiteShell>
  );
}
