"use client";

import { useEffect, useRef } from "react";

const LINES = [
  { html: '<span class="prompt">z&gt;</span> <span class="cmd">add stripe billing</span>' },
  { html: '<span class="dim">… editing checkout, webhook, migration</span>' },
  { html: "" },
  { html: '<span class="hi">Uncertainty tree</span> <span class="dim">(sort=risk)</span>' },
  { html: '<span class="dim">Add Stripe Billing / Backend</span>' },
  {
    html: '  1. Assumed response shape for stripe  <span class="tag">API Assumption</span>  <span class="hi">risk=High</span>',
  },
  {
    html: '  2. Database schema / migration change  <span class="tag">Migration Risk</span>  <span class="hi">risk=Medium</span>',
  },
  { html: '<span class="dim">Add Stripe Billing / Tests</span>' },
  {
    html: '  3. No relevant tests found  <span class="tag">Missing Test</span>  <span class="hi">risk=Medium</span>',
  },
  { html: "" },
  { html: '<span class="ok">3 nodes · /uncertainties to review</span>' },
];

export function TerminalDemo() {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function run() {
      if (!body || cancelled) return;
      body.innerHTML = "";
      const cursor = document.createElement("span");
      cursor.className = "term-cursor";
      body.appendChild(cursor);
      let i = 0;

      function next() {
        if (cancelled || !body) return;
        if (i >= LINES.length) {
          timer = setTimeout(() => {
            if (!cancelled) run();
          }, 4200);
          return;
        }
        const line = document.createElement("span");
        line.className = "line";
        line.innerHTML = LINES[i].html || "&nbsp;";
        body.insertBefore(line, cursor);
        void line.offsetWidth;
        line.classList.add("visible");
        i += 1;
        const delay =
          LINES[i - 1].html === ""
            ? 280
            : 420 + Math.min(220, (LINES[i - 1].html || "").length * 4);
        timer = setTimeout(next, delay);
      }
      timer = setTimeout(next, 500);
    }

    run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return (
    <div className="term" aria-label="Uncertainty tree demo" role="img">
      <div className="term-bar">
        <span className="term-dots" aria-hidden="true">
          <span className="term-dot" />
          <span className="term-dot" />
          <span className="term-dot" />
        </span>
        <span className="term-title">z — uncertainty tree</span>
      </div>
      <div className="term-body" ref={bodyRef} />
    </div>
  );
}
