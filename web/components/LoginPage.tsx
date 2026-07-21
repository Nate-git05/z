"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { postJson } from "@/lib/api";
import { saveSession, type ZSession } from "@/lib/auth";

type Props = {
  redirectUri?: string;
  callbackState?: string;
  method?: string;
  intent?: "signin" | "signup";
};

type ZChannel = "email" | "phone";
type Step = "choose" | "code" | "done";

export function LoginPage({
  redirectUri = "",
  callbackState = "",
  method = "",
  intent = "signin",
}: Props) {
  const isSignup = intent === "signup";
  const initialMethod = method === "google" || method === "z" ? method : "";
  const [zOpen, setZOpen] = useState(initialMethod === "z");
  const [channel, setChannel] = useState<ZChannel>("email");
  const [step, setStep] = useState<Step>("choose");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("000000");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function notifyCli(session: ZSession) {
    if (!redirectUri || !callbackState) return;
    try {
      await fetch(redirectUri, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: callbackState, data: session }),
      });
    } catch {
      /* CLI may have closed */
    }
  }

  async function finish(session: ZSession) {
    saveSession(session);
    await notifyCli(session);
    setStep("done");
  }

  async function onSendEmail(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const result = await postJson<{ detail?: string }>("/v1/auth/email/start", {
      email: email.trim(),
      name: name.trim() || null,
      method: "otp",
    });
    setBusy(false);
    if (!result.ok) {
      setError(
        typeof result.data?.detail === "string"
          ? result.data.detail
          : "Could not send email code."
      );
      return;
    }
    setStep("code");
  }

  async function onVerifyEmail(e: FormEvent) {
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
    await finish(result.data);
  }

  async function onSendPhone(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const result = await postJson<{ detail?: string }>("/v1/auth/phone/start", {
      phone: phone.trim(),
    });
    setBusy(false);
    if (!result.ok) {
      setError(
        typeof result.data?.detail === "string"
          ? result.data.detail
          : "Could not send SMS code."
      );
      return;
    }
    setStep("code");
  }

  async function onVerifyPhone(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const result = await postJson<ZSession & { detail?: string }>(
      "/v1/auth/phone/verify",
      { phone: phone.trim(), code: code.trim() }
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
    await finish(result.data);
  }

  const googleHref = (() => {
    const params = new URLSearchParams();
    if (redirectUri) params.set("redirect_uri", redirectUri);
    if (callbackState) params.set("state", callbackState);
    const q = params.toString();
    return q ? `/app/login/google/start?${q}` : "/app/login/google/start";
  })();

  useEffect(() => {
    if (initialMethod === "google" && typeof window !== "undefined") {
      window.location.replace(googleHref);
    }
  }, [initialMethod, googleHref]);

  useEffect(() => {
    if (step === "done") {
      const t = setTimeout(() => {
        try {
          window.close();
        } catch {
          /* ignore */
        }
      }, 1200);
      return () => clearTimeout(t);
    }
  }, [step]);

  return (
    <div className="auth-shell">
      <div className="auth-mark" aria-hidden="true">
        Z
      </div>
      <div className="auth-card">
        {step === "done" ? (
          <div className="auth-success">
            <p className="auth-title">
              {isSignup ? "Account created." : "Signed in."}
            </p>
            <p className="auth-sub">
              Return to your terminal — this tab will close.
            </p>
            {!redirectUri ? (
              <p className="auth-sub" style={{ marginTop: "1rem" }}>
                <Link href="/" style={{ color: "var(--accent)" }}>
                  Back to home
                </Link>
              </p>
            ) : null}
          </div>
        ) : (
          <>
            <h1 className="auth-title">
              {initialMethod === "z"
                ? isSignup
                  ? "Create your Z account"
                  : "Sign in with Z"
                : isSignup
                  ? "Create your Z account"
                  : "Sign in to Z"}
            </h1>
            <p className="auth-sub">
              {initialMethod === "z"
                ? "Use email or phone to continue."
                : isSignup
                  ? "How would you like to sign up?"
                  : "How would you like to sign in?"}
            </p>

            {error ? (
              <div className="auth-error" role="alert">
                {error}
              </div>
            ) : null}

            {initialMethod !== "z" ? (
              <>
                <a className="auth-btn" href={googleHref}>
                  Continue with Google
                </a>
                <button
                  type="button"
                  className="auth-btn"
                  onClick={() => setZOpen((v) => !v)}
                >
                  Continue with Z
                </button>
              </>
            ) : null}

            {zOpen ? (
              <div className="auth-z-panel">
                <div className="auth-tabs" role="tablist">
                  <button
                    type="button"
                    className={`auth-tab${channel === "email" ? " active" : ""}`}
                    onClick={() => {
                      setChannel("email");
                      setStep("choose");
                      setError("");
                    }}
                  >
                    Email
                  </button>
                  <button
                    type="button"
                    className={`auth-tab${channel === "phone" ? " active" : ""}`}
                    onClick={() => {
                      setChannel("phone");
                      setStep("choose");
                      setError("");
                    }}
                  >
                    Phone
                  </button>
                </div>

                {channel === "email" ? (
                  step === "choose" ? (
                    <form className="auth-form-block" onSubmit={onSendEmail}>
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
                      <label htmlFor="login-name">
                        Name <span className="opt">(optional)</span>
                      </label>
                      <input
                        id="login-name"
                        type="text"
                        autoComplete="name"
                        placeholder="Ada"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                      <button
                        className="auth-btn auth-btn-primary"
                        type="submit"
                        disabled={busy}
                      >
                        {busy ? "Sending…" : "Send code"}
                      </button>
                    </form>
                  ) : (
                    <form className="auth-form-block" onSubmit={onVerifyEmail}>
                      <label htmlFor="login-email-code">Verification code</label>
                      <input
                        id="login-email-code"
                        type="text"
                        inputMode="numeric"
                        required
                        autoComplete="one-time-code"
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                      />
                      <button
                        className="auth-btn auth-btn-primary"
                        type="submit"
                        disabled={busy}
                      >
                        {busy ? "Verifying…" : "Verify"}
                      </button>
                    </form>
                  )
                ) : step === "choose" ? (
                  <form className="auth-form-block" onSubmit={onSendPhone}>
                    <label htmlFor="login-phone">Phone</label>
                    <input
                      id="login-phone"
                      type="tel"
                      required
                      autoComplete="tel"
                      placeholder="+15551234567"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                    />
                    <button
                      className="auth-btn auth-btn-primary"
                      type="submit"
                      disabled={busy}
                    >
                      {busy ? "Sending…" : "Send code"}
                    </button>
                  </form>
                ) : (
                  <form className="auth-form-block" onSubmit={onVerifyPhone}>
                    <label htmlFor="login-phone-code">Verification code</label>
                    <input
                      id="login-phone-code"
                      type="text"
                      inputMode="numeric"
                      required
                      autoComplete="one-time-code"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                    />
                    <button
                      className="auth-btn auth-btn-primary"
                      type="submit"
                      disabled={busy}
                    >
                      {busy ? "Verifying…" : "Verify"}
                    </button>
                  </form>
                )}
              </div>
            ) : null}

            <p className="auth-legal">
              {isSignup ? (
                <>
                  Already have an account? <Link href="/login">Sign in</Link>
                </>
              ) : (
                <>
                  New here? <Link href="/signup">Create an account</Link>
                </>
              )}{" "}
              · By continuing you agree to the{" "}
              <Link href="/#">Terms of Service</Link> and{" "}
              <Link href="/#">Privacy Notice</Link>.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
