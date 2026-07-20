"use client";

import Link from "next/link";
import { useState } from "react";
import { SiteShell, openWaitlistEvent } from "@/components/SiteShell";
import { TerminalDemo } from "@/components/TerminalDemo";

function CopyInstall({
  id,
  cmd,
  label,
}: {
  id: string;
  cmd: string;
  label: string;
}) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* ignore */
    }
  }
  return (
    <>
      <p className="lp-install-label">{label}</p>
      <div className="lp-code">
        <code id={id} data-cmd={cmd}>
          <span className="prompt">$</span> {cmd}
        </code>
        <button type="button" className={copied ? "copied" : undefined} onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </>
  );
}

export function LandingPage() {
  return (
    <SiteShell>
      <main className="lp-main">
        <section className="lp-hero" aria-label="Hero">
          <div className="lp-hero-copy">
            <p className="lp-eyebrow">
              <span className="lp-pulse" aria-hidden="true" />
              Open for early testing
            </p>
            <h1>
              A coding agent that{" "}
              <span className="muted">knows what it doesn’t know</span>
            </h1>
            <p className="lp-subhead">
              Z attaches structured, reviewable uncertainty to the code it
              touches — and learns reusable skills from your work so it doesn’t
              ask the same questions twice.
            </p>
            <div className="lp-hero-ctas">
              <a className="lp-btn" href="#install">
                Try early testing
              </a>
              <button
                type="button"
                className="lp-btn lp-btn-outline"
                onClick={() => openWaitlistEvent()}
              >
                Join the waitlist
              </button>
            </div>
          </div>
          <TerminalDemo />
        </section>

        <section className="lp-section" id="why">
          <p className="lp-label">Why Z</p>
          <h2>Wrong changes look like correct ones until something breaks</h2>
          <p className="lp-lede">
            Agents get more autonomy every month. The failure mode isn’t
            occasional mistakes — it’s that a bad edit is indistinguishable from
            a good one until production. Z makes the difference visible before
            you ship.
          </p>
          <div className="lp-cards">
            <article className="lp-card">
              <p className="lp-card-eyebrow">01 — Visibility</p>
              <h3>Review what needs it</h3>
              <p>
                Risk and confidence stay separate. High-risk nodes rise even
                when the agent is “sure.”
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">02 — Signals</p>
              <h3>Checkable, not vibes</h3>
              <p>
                Detectors read tests, paths, APIs, and blast radius — not fake
                confidence percentages.
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">03 — Memory</p>
              <h3>Teach once, reuse</h3>
              <p>
                Skills capture how your repo actually works so the same
                conventions aren’t re-explained every session.
              </p>
            </article>
          </div>
        </section>

        <section className="lp-section" id="how">
          <p className="lp-label">How it works</p>
          <h2>Detectors build a tree you can act on</h2>
          <p className="lp-lede">
            As Z edits your repo, it runs checkable detectors — tests, path
            keywords, live API verification, blast radius — and builds a tree
            you can act on.
          </p>
          <div className="lp-cards">
            <article className="lp-card">
              <p className="lp-card-eyebrow">01</p>
              <h3>You describe the task</h3>
              <p>
                Z decomposes the request into a checklist so requirement gaps
                surface before they ship.
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">02</p>
              <h3>Detectors fire on real signals</h3>
              <p>
                Missing tests, unverified APIs, migrations, TODOs, shared logic —
                each becomes a typed node.
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">03</p>
              <h3>You act from the tree — or Z does</h3>
              <p>
                Browse risk-first, or let Z take a bounded self-correction pass
                on the findings it&apos;s confident enough to fix on its own.
              </p>
            </article>
          </div>
        </section>

        <section className="lp-section" id="skills">
          <p className="lp-label">Skills</p>
          <h2>Reusable skills</h2>
          <p className="lp-lede">
            Coding agents forget your project’s rules the moment the chat ends.
            Teach Z once how your repo works — it remembers, and applies the
            right playbook on the next matching task.
          </p>
          <div className="lp-cards">
            <article className="lp-card">
              <p className="lp-card-eyebrow">01 — Create</p>
              <h3>Paste or capture</h3>
              <p>
                Drop in a playbook with <code className="mono">/skills add</code>,
                generate one with <code className="mono">z skill create</code>, or
                let Z offer to save after a solid turn.
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">02 — Index</p>
              <h3>Metadata inferred for you</h3>
              <p>
                Z writes when to use it, what triggers it, and where the body
                lives — so you don’t fill out a form.
              </p>
            </article>
            <article className="lp-card">
              <p className="lp-card-eyebrow">03 — Apply</p>
              <h3>Auto-applied later</h3>
              <p>
                On a matching task, Z pulls the skill in automatically. No
                manual attach. No re-explaining Stripe webhooks every session.
              </p>
            </article>
          </div>
        </section>

        <section className="lp-section" id="detectors">
          <p className="lp-label">Detectors</p>
          <h2>What gets flagged</h2>
          <p className="lp-lede">
            Risk and confidence stay separate. High-risk nodes rise to the top
            even when confidence is high.
          </p>
          <div className="lp-chips">
            {[
              ["Payment / auth / security", "Path and symbol keyword match — auto Medium risk at minimum."],
              ["Missing or failing tests", "Relevant suite by module/symbol — fail escalates; none flags Missing Test."],
              ["API assumption", "No live-verified call this session, or unverifiable MCP result."],
              ["Pattern inconsistency", "No match or conflicting conventions for a new file."],
              ["Shared logic / blast radius", "Reference count above a configurable threshold."],
              ["Unverifiable config", "Env vars and secrets the agent cannot inspect."],
              ["Migration risk", "Schema changes plus explicit existing-data impact."],
              ["High confidence", "Positive signal when a change matches a tested pattern and passes."],
              ["Reinvented solutions", "Hand-rolled IP/email/date parsers — flagged when the surrounding code is actually about that domain."],
              ["Missing sibling registration", "A new plugin/handler that matches a family but never got added to the shared registry."],
              ["Permissive shortcuts", "getattr defaults and broad excepts that quietly absorb a real failure."],
              ["Scope drift", "Editing the wrong file for the right reason, or continuing after the task is done."],
            ].map(([title, body]) => (
              <div className="lp-chip" key={title}>
                <strong>{title}</strong>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section" id="model-access">
          <p className="lp-label">Model access</p>
          <h2>Bring your own model, or let Z route for you.</h2>
          <div className="lp-model-cards">
            <article className="lp-model-card">
              <h3>Bring your own key</h3>
              <p className="lp-model-tag">Free, forever</p>
              <p>
                Connect Anthropic, OpenAI, DeepSeek, Groq, Gemini, Kimi/Moonshot,
                or a local Ollama server. Your key goes straight from your
                machine to the provider — Z never sees it.
              </p>
            </article>
            <article className="lp-model-card lp-model-card-highlight">
              <h3>Z&apos;s router</h3>
              <p className="lp-model-tag">Automatic routing</p>
              <p>
                Z picks the right model for each task automatically and manages
                billing across providers for you.
              </p>
              <Link href="/pricing" className="lp-model-link">
                See pricing →
              </Link>
            </article>
          </div>
        </section>

        <section className="lp-section" id="install">
          <p className="lp-label">Install</p>
          <h2>Try early testing</h2>
          <p className="lp-lede">
            Install now and run Z in a project. Bring your own model keys. Rough
            edges are expected — that’s the point of early testing.
          </p>
          <CopyInstall
            id="install-cmd-curl"
            label="curl"
            cmd="curl -fsSL https://raw.githubusercontent.com/Nate-git05/z/main/install.sh | sh"
          />
          <CopyInstall
            id="install-cmd-pip"
            label="pip"
            cmd='pip install -U "git+https://github.com/Nate-git05/z.git"'
          />
          <p className="lp-lede lp-lede-after">
            macOS / Linux. Puts <code className="mono">z</code> on your PATH.
            Then set your API key and run <code className="mono">z</code> in a
            project.
          </p>
        </section>
      </main>
    </SiteShell>
  );
}
