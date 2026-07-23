"use client";

import { useState } from "react";

export function CopyInstall({
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
