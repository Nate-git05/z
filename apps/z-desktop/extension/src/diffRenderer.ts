/**
 * Parses unified-diff text (from `git show -p` or GitHub's PR diff) into a
 * simple line-numbered, colored HTML view — no diff algorithm needed since
 * the source already comes as a diff.
 */

interface DiffLine {
  kind: "context" | "add" | "del" | "hunk" | "meta";
  text: string;
  oldNo?: number;
  newNo?: number;
}

interface DiffFile {
  path: string;
  lines: DiffLine[];
}

function escapeHtml(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const FILE_HEADER_RE = /^diff --git a\/(.+?) b\/(.+)$/;
const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

export function parseUnifiedDiff(diffText: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;

  const rawLines = (diffText || "").split("\n");
  for (const line of rawLines) {
    const fileMatch = FILE_HEADER_RE.exec(line);
    if (fileMatch) {
      current = { path: fileMatch[2] || fileMatch[1], lines: [] };
      files.push(current);
      continue;
    }
    if (!current) {
      continue;
    }
    // Skip the "index abc123..def456" / "---" / "+++" preamble lines.
    if (
      line.startsWith("index ") ||
      line.startsWith("--- ") ||
      line.startsWith("+++ ") ||
      line.startsWith("new file mode") ||
      line.startsWith("deleted file mode") ||
      line.startsWith("similarity index") ||
      line.startsWith("rename ")
    ) {
      continue;
    }
    const hunkMatch = HUNK_RE.exec(line);
    if (hunkMatch) {
      oldNo = parseInt(hunkMatch[1], 10);
      newNo = parseInt(hunkMatch[2], 10);
      current.lines.push({ kind: "hunk", text: line });
      continue;
    }
    if (line.startsWith("+")) {
      current.lines.push({ kind: "add", text: line.slice(1), newNo });
      newNo += 1;
    } else if (line.startsWith("-")) {
      current.lines.push({ kind: "del", text: line.slice(1), oldNo });
      oldNo += 1;
    } else if (line.startsWith("\\ No newline")) {
      current.lines.push({ kind: "meta", text: line });
    } else {
      current.lines.push({ kind: "context", text: line.replace(/^ /, ""), oldNo, newNo });
      oldNo += 1;
      newNo += 1;
    }
  }
  return files;
}

export function renderDiffHtml(diffText: string): string {
  const files = parseUnifiedDiff(diffText);
  if (!files.length) {
    return `<div class="diff-empty">No diff available.</div>`;
  }
  return files
    .map((file) => {
      const rows = file.lines
        .map((l) => {
          if (l.kind === "hunk") {
            return `<div class="diff-row hunk"><span class="ln"></span><span class="ln"></span><span class="txt">${escapeHtml(l.text)}</span></div>`;
          }
          if (l.kind === "meta") {
            return `<div class="diff-row meta"><span class="ln"></span><span class="ln"></span><span class="txt">${escapeHtml(l.text)}</span></div>`;
          }
          const cls = l.kind === "add" ? "add" : l.kind === "del" ? "del" : "ctx";
          const prefix = l.kind === "add" ? "+" : l.kind === "del" ? "-" : " ";
          return (
            `<div class="diff-row ${cls}">` +
            `<span class="ln">${l.oldNo ?? ""}</span>` +
            `<span class="ln">${l.newNo ?? ""}</span>` +
            `<span class="txt">${prefix}${escapeHtml(l.text)}</span>` +
            `</div>`
          );
        })
        .join("");
      return (
        `<div class="diff-file">` +
        `<div class="diff-file-header">${escapeHtml(file.path)}</div>` +
        `<div class="diff-body">${rows}</div>` +
        `</div>`
      );
    })
    .join("");
}

export function diffCss(): string {
  return `
.diff-empty { padding: 16px; color: var(--z-text-secondary); }
.diff-file { border: 1px solid var(--z-border); border-radius: var(--z-radius-sm); margin: 0 0 14px; overflow: hidden; }
.diff-file-header {
  padding: 8px 12px; font-size: 12px; font-weight: 600; color: var(--z-text);
  background: var(--z-surface); border-bottom: 1px solid var(--z-border);
}
.diff-body { font-family: var(--z-font-mono); font-size: 12px; overflow-x: auto; }
.diff-row { display: flex; white-space: pre; }
.diff-row .ln {
  flex: 0 0 42px; text-align: right; padding: 0 8px; color: var(--z-muted);
  user-select: none; border-right: 1px solid var(--z-border);
}
.diff-row .txt { flex: 1 1 auto; padding: 0 10px; }
.diff-row.hunk { color: var(--z-accent); background: var(--z-accent-wash); }
.diff-row.hunk .txt { padding: 2px 10px; }
.diff-row.meta { color: var(--z-muted); font-style: italic; }
.diff-row.add { background: rgba(143, 174, 139, 0.12); }
.diff-row.add .txt { color: var(--z-status-ok); }
.diff-row.del { background: rgba(217, 119, 87, 0.12); }
.diff-row.del .txt { color: var(--z-status-blocked); }
.diff-row.ctx .txt { color: var(--z-text-secondary); }
`.trim();
}
