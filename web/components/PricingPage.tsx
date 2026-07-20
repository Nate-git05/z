"use client";

import Link from "next/link";
import { SiteShell, openWaitlistEvent } from "@/components/SiteShell";

export function PricingPage() {
  return (
    <SiteShell>
      <main className="pricing-page">
        <h1>Pricing.</h1>
        <p className="pricing-intro">
          The core Z agent is free no matter what. The choice below is only
          about which model powers your requests.
        </p>

        <div className="pricing-columns">
          <div className="pricing-card">
            <h2>Bring your own key</h2>
            <p className="pricing-price">
              Free <span>forever</span>
            </p>
            <p>
              Use Z&apos;s full agent — the uncertainty tree, skills, every
              detector — with your own API key from any supported provider.
            </p>
            <ul className="pricing-features">
              <li>Full uncertainty tree and all detectors</li>
              <li>Skills capture and auto-apply</li>
              <li>Self-correction on findings Z is confident about</li>
              <li>
                Support for Anthropic, OpenAI, DeepSeek, Groq, Gemini,
                Kimi/Moonshot, and local Ollama servers
              </li>
              <li>Your key never touches Z&apos;s servers</li>
              <li>No usage limits from Z</li>
            </ul>
            <Link href="/#install" className="lp-btn">
              Get started free
            </Link>
          </div>

          <div className="pricing-card pricing-card-highlight">
            <span className="pricing-badge">Recommended</span>
            <h2>Z Router</h2>
            <p className="pricing-price">Coming soon</p>
            <p>
              Skip managing your own API keys. Z automatically picks the right
              model for each task and bills you once across every provider.
            </p>
            <ul className="pricing-features">
              <li>Everything in Bring your own key, plus:</li>
              <li>Automatic model selection per task, based on real signals</li>
              <li>One bill instead of juggling multiple provider accounts</li>
              <li>Escalates to a stronger model automatically on failure</li>
              <li>Cost transparency, including any escalations</li>
              <li>Your code is never stored — routed ephemerally</li>
            </ul>
            <button
              type="button"
              className="lp-btn"
              onClick={() => openWaitlistEvent("router")}
            >
              Notify me when it&apos;s ready
            </button>
          </div>
        </div>

        <div className="pricing-faq">
          <h2>FAQ</h2>
          <details>
            <summary>Is the core Z agent free?</summary>
            <p>
              Yes. The uncertainty tree, skills, detectors, and self-correction
              are free whether you bring your own key or eventually use Z&apos;s
              router. Pricing only applies to model access through the router.
            </p>
          </details>
          <details>
            <summary>Do you store my API key or my code?</summary>
            <p>
              No. With bring-your-own-key, your provider key stays on your
              machine and talks directly to the provider. Even with the router,
              your code is routed ephemerally and never stored.
            </p>
          </details>
          <details>
            <summary>When will the Z Router launch?</summary>
            <p>
              We&apos;re finishing the routing and billing stack. Join the
              waitlist from this page — choose “Notify me when it&apos;s ready”
              — and we&apos;ll email you when seats open.
            </p>
          </details>
          <details>
            <summary>Can I switch between BYOK and the router later?</summary>
            <p>
              Yes. In the CLI, <code className="mono">z auth switch</code> lets
              you re-choose bring-your-own-key or the router without losing your
              account or skills.
            </p>
          </details>
        </div>
      </main>
    </SiteShell>
  );
}
