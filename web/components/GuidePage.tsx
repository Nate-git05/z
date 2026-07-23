"use client";

import Link from "next/link";
import { SiteShell, openWaitlistEvent } from "@/components/SiteShell";
import { CopyInstall } from "@/components/CopyInstall";

const STEPS = [
  {
    title: "Sign in or create an account",
    body: (
      <>
        Run <code className="mono">z</code> in your project. It opens your
        browser to log in or sign up — close the tab once it confirms
        you&apos;re signed in, and head back to your terminal.
      </>
    ),
  },
  {
    title: "Choose bring-your-own-key or Z's router",
    body: (
      <>
        Z asks once, right there in the terminal. Pick BYOK to use your own
        provider key for free, or the router to let Z pick and bill across
        providers automatically.
      </>
    ),
  },
  {
    title: "Add a provider key (BYOK only)",
    body: (
      <>
        Pick from Anthropic, OpenAI, DeepSeek, Groq, Gemini, Kimi/Moonshot, or
        a local Ollama server, then paste your key. It goes straight from
        your machine to the provider — Z never sees it.
      </>
    ),
  },
  {
    title: "Start a task",
    body: (
      <>
        Describe what you want changed in plain English. Z decomposes it
        into a checklist, edits your repo, and flags anything worth a second
        look in the uncertainty tree before it commits.
      </>
    ),
  },
];

const COMMANDS: [string, string][] = [
  ["/uncertainties", "Open the review tree for the current session — risk-first, not vibes-first."],
  ["/skills add", "Paste in a playbook Z should remember for this repo."],
  ["z skill create", "Generate a new skill from your connected model instead of writing one by hand."],
  ["/commit", "Commit through the verification gate — a blocked commit shows exactly what's missing."],
  ["z byok add", "Add another provider key without changing your default model."],
  ["z models", "List the curated model set, or search the full catalog with --all."],
];

const SWITCHING: [string, string][] = [
  ["z auth switch", "Re-pick BYOK vs. router, and the provider/model within it. Keeps your account and skills."],
  ["z reset", "Clear your saved router/model choice and pick again. Keeps you signed in."],
  ["z full-reset", "Sign out completely and start fresh — use this to switch accounts or set up another profile."],
];

export function GuidePage() {
  return (
    <SiteShell>
      <main className="guide-page">
        <h1>Using Z.</h1>
        <p className="pricing-intro">
          Everything for your first session — from install to your first
          reviewed commit — in the order you&apos;ll actually hit it.
        </p>

        <section className="lp-section guide-section-first" id="install">
          <p className="lp-label">Install</p>
          <h2>Get Z on your machine</h2>
          <p className="lp-lede">
            macOS / Linux. Puts <code className="mono">z</code> on your PATH.
          </p>
          <CopyInstall
            id="guide-install-curl"
            label="curl"
            cmd="curl -fsSL https://raw.githubusercontent.com/Nate-git05/z/main/install.sh | sh"
          />
          <CopyInstall
            id="guide-install-pip"
            label="pip"
            cmd='pip install -U "git+https://github.com/Nate-git05/z.git"'
          />
        </section>

        <section className="lp-section" id="first-run">
          <p className="lp-label">First run</p>
          <h2>From install to your first task</h2>
          <p className="lp-lede">
            Four steps, once, the first time you run <code className="mono">z</code>{" "}
            in a project.
          </p>
          <div className="guide-steps">
            {STEPS.map((step, i) => (
              <article className="guide-step" key={step.title}>
                <span className="guide-step-num">{i + 1}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="lp-section" id="commands">
          <p className="lp-label">Everyday commands</p>
          <h2>Once you&apos;re in a session</h2>
          <p className="lp-lede">
            The commands you&apos;ll reach for most, in the terminal or inside
            a Z session.
          </p>
          <div className="lp-chips">
            {COMMANDS.map(([title, body]) => (
              <div className="lp-chip" key={title}>
                <strong className="mono">{title}</strong>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section" id="switching">
          <p className="lp-label">Switching later</p>
          <h2>Change your mind without losing anything</h2>
          <p className="lp-lede">
            Your account, skills, and history stay put — these only touch
            model access and sign-in state.
          </p>
          <div className="lp-chips">
            {SWITCHING.map(([title, body]) => (
              <div className="lp-chip" key={title}>
                <strong className="mono">{title}</strong>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section" id="next">
          <p className="lp-label">Go deeper</p>
          <h2>More on how Z decides what to flag</h2>
          <p className="lp-lede">
            The full detector list and the reasoning behind Z&apos;s
            uncertainty tree live on the homepage.
          </p>
          <div className="lp-hero-ctas">
            <Link href="/#detectors" className="lp-btn">
              See all detectors
            </Link>
            <button
              type="button"
              className="lp-btn lp-btn-outline"
              onClick={() => openWaitlistEvent()}
            >
              Join the waitlist
            </button>
          </div>
        </section>
      </main>
    </SiteShell>
  );
}
