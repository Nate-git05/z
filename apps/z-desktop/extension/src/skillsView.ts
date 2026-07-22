/**
 * Phase 7 — Skills viewer + author (filters, detail, draft create with near-dup).
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";

interface SkillSummary {
  id?: string;
  title?: string;
  kind?: string;
  description?: string;
  triggers?: string[];
  capability?: string;
  quality_state?: string;
  needs_review?: boolean;
  source?: string;
  updated_at?: string;
  symptom_description?: string;
  root_cause_category?: string;
}

interface SkillDetail extends SkillSummary {
  content?: string;
  languages?: string[];
  tags?: string[];
}

interface NearDup {
  id?: string;
  title?: string;
  kind?: string;
  quality_state?: string;
  score?: number;
  reason?: string;
}

export class SkillsViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private skills: SkillSummary[] = [];
  private selected: SkillDetail | null = null;
  private error: string | null = null;
  private status: string | null = null;
  private nearDup: NearDup | null = null;
  private pendingDraft: Record<string, unknown> | null = null;
  private filters = {
    kind: "",
    quality_state: "",
    needs_review: "" as "" | "true" | "false",
    query: "",
  };
  private mode: "list" | "detail" | "create" = "list";

  constructor(private readonly manager: AppServerManager) {
    manager.onDidChange(() => void this.refresh());
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.shellHtml();
    webviewView.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
    void this.refresh();
  }

  private async onMessage(msg: Record<string, unknown>): Promise<void> {
    const type = String(msg?.type || "");
    if (type === "refresh") {
      await this.refresh();
      return;
    }
    if (type === "filters") {
      this.filters = {
        kind: String(msg.kind || ""),
        quality_state: String(msg.quality_state || ""),
        needs_review: (String(msg.needs_review || "") as "" | "true" | "false") || "",
        query: String(msg.query || ""),
      };
      await this.refresh();
      return;
    }
    if (type === "open" && msg.id) {
      await this.openDetail(String(msg.id));
      return;
    }
    if (type === "back") {
      this.mode = "list";
      this.selected = null;
      this.nearDup = null;
      this.pendingDraft = null;
      this.status = null;
      this.post();
      return;
    }
    if (type === "showCreate") {
      this.mode = "create";
      this.nearDup = null;
      this.pendingDraft = null;
      this.status = null;
      this.post();
      return;
    }
    if (type === "create") {
      await this.createSkill(msg as Record<string, unknown>, { force: false, merge: false });
      return;
    }
    if (type === "createForce") {
      await this.createSkill(
        (this.pendingDraft as Record<string, unknown>) || (msg as Record<string, unknown>),
        { force: true, merge: false }
      );
      return;
    }
    if (type === "createMerge") {
      await this.createSkill(
        (this.pendingDraft as Record<string, unknown>) || (msg as Record<string, unknown>),
        { force: false, merge: true }
      );
    }
  }

  private async openDetail(id: string): Promise<void> {
    if (!this.manager.rpc) {
      return;
    }
    try {
      const result = (await this.manager.rpc.request("skills/get", { id })) as {
        skill?: SkillDetail;
      };
      this.selected = result.skill || null;
      this.mode = "detail";
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.post();
  }

  private async createSkill(
    draft: Record<string, unknown>,
    opts: { force: boolean; merge: boolean }
  ): Promise<void> {
    if (!this.manager.rpc) {
      try {
        await this.manager.ensureConnected();
      } catch (err) {
        this.error = err instanceof Error ? err.message : String(err);
        this.post();
        return;
      }
    }
    const skill = {
      title: String(draft.title || "").trim(),
      description: String(draft.description || "").trim(),
      content: String(draft.content || "").trim(),
      kind: String(draft.kind || "playbook").trim() || "playbook",
      triggers: String(draft.triggers || "")
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      capability: String(draft.capability || "").trim(),
      symptom_description: String(draft.symptom_description || "").trim(),
      root_cause_category: String(draft.root_cause_category || "").trim(),
    };
    if (!skill.title) {
      this.status = "Title is required.";
      this.post();
      return;
    }
    this.pendingDraft = skill;
    try {
      const result = (await this.manager.rpc!.request("skills/create", {
        skill,
        force: opts.force,
        merge: opts.merge,
      })) as {
        created?: boolean;
        merged?: boolean;
        skill?: SkillDetail;
        near_dup?: NearDup | null;
        message?: string;
        draft?: Record<string, unknown>;
      };
      if (!result.created && result.near_dup) {
        this.nearDup = result.near_dup;
        this.status = result.message || "Near-duplicate found.";
        this.mode = "create";
        this.post();
        return;
      }
      this.nearDup = null;
      this.pendingDraft = null;
      this.status = result.merged
        ? `Merged into “${result.skill?.title || "existing skill"}” (draft).`
        : `Created “${result.skill?.title || skill.title}” as draft.`;
      this.mode = "list";
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  async refresh(): Promise<void> {
    if (!this.manager.rpc) {
      this.skills = [];
      this.error = null;
      this.post();
      return;
    }
    try {
      const params: Record<string, unknown> = {};
      if (this.filters.kind) {
        params.kind = this.filters.kind;
      }
      if (this.filters.quality_state) {
        params.quality_state = this.filters.quality_state;
      }
      if (this.filters.query) {
        params.query = this.filters.query;
      }
      if (this.filters.needs_review === "true") {
        params.needs_review = true;
      } else if (this.filters.needs_review === "false") {
        params.needs_review = false;
      }
      const result = (await this.manager.rpc.request("skills/list", params)) as {
        skills?: SkillSummary[];
      };
      this.skills = Array.isArray(result.skills) ? result.skills : [];
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.post();
  }

  private post(): void {
    if (!this.view) {
      return;
    }
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      skills: this.skills,
      selected: this.selected,
      mode: this.mode,
      filters: this.filters,
      nearDup: this.nearDup,
      status: this.status,
      error: this.error,
    });
  }

  private shellHtml(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  html, body {
    height: 100%; margin: 0; padding: 0;
    font-size: 12.5px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px 6px; gap: 8px;
  }
  h3 { margin: 0; font-size: 13px; font-weight: 600; color: var(--z-accent-bright); }
  #filters {
    display: flex; flex-direction: column; gap: 6px; padding: 0 14px 10px;
  }
  #filters row, .row { display: flex; gap: 6px; flex-wrap: wrap; }
  input, select, textarea {
    padding: 5px 8px; font-size: 11.5px; font-family: inherit; box-sizing: border-box;
  }
  input, select { flex: 1; min-width: 0; }
  textarea { width: 100%; min-height: 72px; resize: vertical; }
  button { padding: 5px 12px; font-size: 11.5px; }
  #list, #detail, #create { padding: 0 14px 18px; overflow-y: auto; }
  .item {
    padding: 10px 0; border-bottom: 1px solid var(--z-border);
    cursor: pointer;
  }
  .item:hover .title { color: var(--z-accent-bright); }
  .item .title { font-weight: 600; }
  .item .meta { color: var(--z-text-secondary); margin-top: 3px; font-size: 11px; }
  .badge {
    display: inline-block; font-size: 10px; padding: 1px 5px; margin-right: 4px;
    border: 1px solid var(--z-accent-dim); color: var(--z-accent);
  }
  .empty, .err, .status { padding: 8px 0; line-height: 1.5; color: var(--z-text-secondary); }
  .err { color: var(--z-status-blocked); }
  .status { color: var(--z-status-ok); }
  .field { margin: 0 0 8px; }
  .field label {
    display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--z-accent); margin-bottom: 3px;
  }
  .near {
    margin: 8px 0; padding: 8px;
    border: 1px dashed var(--z-accent);
    background: var(--z-raised);
  }
  .hidden { display: none; }
  pre {
    white-space: pre-wrap; word-break: break-word; font-size: 11px;
    background: var(--z-raised); padding: 8px; margin: 6px 0 0; border: 1px solid var(--z-border);
  }
</style>
</head>
<body>
  <div id="hdr">
    <h3>Skills</h3>
    <div style="display:flex;gap:6px">
      <button class="secondary" id="newBtn">New</button>
      <button class="secondary" id="refresh">↻</button>
    </div>
  </div>
  <div id="filters">
    <div class="row">
      <select id="kind">
        <option value="">All kinds</option>
        <option value="playbook">playbook</option>
        <option value="scaffold">scaffold</option>
        <option value="bug_pattern">bug_pattern</option>
      </select>
      <select id="quality">
        <option value="">All states</option>
        <option value="draft">draft</option>
        <option value="verified">verified</option>
        <option value="rejected">rejected</option>
      </select>
    </div>
    <div class="row">
      <select id="review">
        <option value="">Review: any</option>
        <option value="true">needs review</option>
        <option value="false">reviewed</option>
      </select>
      <input id="query" placeholder="Search title, triggers…" />
    </div>
  </div>
  <div id="statusBox"></div>
  <div id="list"></div>
  <div id="detail" class="hidden"></div>
  <div id="create" class="hidden">
    <div class="field"><label>Title</label><input id="cTitle" /></div>
    <div class="field"><label>Kind</label>
      <select id="cKind">
        <option value="playbook">playbook</option>
        <option value="scaffold">scaffold</option>
        <option value="bug_pattern">bug_pattern</option>
      </select>
    </div>
    <div class="field"><label>Description</label><textarea id="cDesc"></textarea></div>
    <div class="field"><label>Content</label><textarea id="cContent" style="min-height:100px"></textarea></div>
    <div class="field"><label>Triggers (comma-separated)</label><input id="cTriggers" /></div>
    <div class="field"><label>Capability</label><input id="cCap" /></div>
    <div class="field"><label>Symptom (bug_pattern)</label><input id="cSymptom" /></div>
    <div class="field"><label>Root cause category</label><input id="cCat" /></div>
    <div id="nearBox" class="near hidden"></div>
    <div class="row">
      <button id="cSave">Save draft</button>
      <button class="secondary" id="cCancel">Cancel</button>
      <button class="secondary hidden" id="cForce">Create anyway</button>
      <button class="secondary hidden" id="cMerge">Merge into existing</button>
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const listEl = document.getElementById('list');
    const detailEl = document.getElementById('detail');
    const createEl = document.getElementById('create');
    const statusBox = document.getElementById('statusBox');
    const nearBox = document.getElementById('nearBox');
    const filtersEl = document.getElementById('filters');

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function emitFilters() {
      vscode.postMessage({
        type: 'filters',
        kind: document.getElementById('kind').value,
        quality_state: document.getElementById('quality').value,
        needs_review: document.getElementById('review').value,
        query: document.getElementById('query').value,
      });
    }
    document.getElementById('kind').onchange = emitFilters;
    document.getElementById('quality').onchange = emitFilters;
    document.getElementById('review').onchange = emitFilters;
    let qTimer = null;
    document.getElementById('query').oninput = () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(emitFilters, 250);
    };
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });
    document.getElementById('newBtn').onclick = () => vscode.postMessage({ type: 'showCreate' });
    document.getElementById('cCancel').onclick = () => vscode.postMessage({ type: 'back' });
    document.getElementById('cSave').onclick = () => {
      vscode.postMessage({
        type: 'create',
        title: document.getElementById('cTitle').value,
        kind: document.getElementById('cKind').value,
        description: document.getElementById('cDesc').value,
        content: document.getElementById('cContent').value,
        triggers: document.getElementById('cTriggers').value,
        capability: document.getElementById('cCap').value,
        symptom_description: document.getElementById('cSymptom').value,
        root_cause_category: document.getElementById('cCat').value,
      });
    };
    document.getElementById('cForce').onclick = () => vscode.postMessage({ type: 'createForce' });
    document.getElementById('cMerge').onclick = () => vscode.postMessage({ type: 'createMerge' });

    function showMode(mode) {
      listEl.classList.toggle('hidden', mode !== 'list');
      detailEl.classList.toggle('hidden', mode !== 'detail');
      createEl.classList.toggle('hidden', mode !== 'create');
      filtersEl.classList.toggle('hidden', mode === 'create');
    }

    function renderList(skills) {
      if (!skills.length) {
        listEl.innerHTML = '<div class="empty">No skills match. Create a draft playbook or bug pattern.</div>';
        return;
      }
      listEl.innerHTML = skills.map(s => {
        return '<div class="item" data-id="' + escapeHtml(s.id || '') + '">'
          + '<div class="title">' + escapeHtml(s.title || 'Untitled') + '</div>'
          + '<div class="meta">'
          + '<span class="badge">' + escapeHtml(s.kind || '') + '</span>'
          + '<span class="badge">' + escapeHtml(s.quality_state || '') + '</span>'
          + (s.needs_review ? '<span class="badge">needs review</span>' : '')
          + (s.capability ? ' · ' + escapeHtml(s.capability) : '')
          + '</div></div>';
      }).join('');
      for (const el of listEl.querySelectorAll('.item[data-id]')) {
        el.onclick = () => vscode.postMessage({ type: 'open', id: el.dataset.id });
      }
    }

    function renderDetail(s) {
      if (!s) { detailEl.innerHTML = ''; return; }
      detailEl.innerHTML =
        '<button class="secondary" id="backBtn">← Back</button>'
        + '<h3 style="margin:10px 0 6px">' + escapeHtml(s.title || '') + '</h3>'
        + '<div class="meta">'
        + '<span class="badge">' + escapeHtml(s.kind || '') + '</span>'
        + '<span class="badge">' + escapeHtml(s.quality_state || '') + '</span>'
        + (s.needs_review ? '<span class="badge">needs review</span>' : '')
        + '</div>'
        + (s.description ? '<p>' + escapeHtml(s.description) + '</p>' : '')
        + (s.triggers && s.triggers.length ? '<div class="field"><label>Triggers</label><div>' + escapeHtml(s.triggers.join(', ')) + '</div></div>' : '')
        + (s.capability ? '<div class="field"><label>Capability</label><div>' + escapeHtml(s.capability) + '</div></div>' : '')
        + (s.content ? '<div class="field"><label>Content</label><pre>' + escapeHtml(s.content) + '</pre></div>' : '');
      document.getElementById('backBtn').onclick = () => vscode.postMessage({ type: 'back' });
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      const f = d.filters || {};
      document.getElementById('kind').value = f.kind || '';
      document.getElementById('quality').value = f.quality_state || '';
      document.getElementById('review').value = f.needs_review || '';
      document.getElementById('query').value = f.query || '';
      let statusHtml = '';
      if (d.error) statusHtml += '<div class="err">' + escapeHtml(d.error) + '</div>';
      if (d.status) statusHtml += '<div class="status">' + escapeHtml(d.status) + '</div>';
      statusBox.innerHTML = statusHtml;
      showMode(d.mode || 'list');
      if (d.mode === 'detail') renderDetail(d.selected);
      else if (d.mode === 'list') renderList(d.skills || []);
      if (d.nearDup) {
        nearBox.classList.remove('hidden');
        nearBox.innerHTML = '<strong>Near duplicate</strong><div>'
          + escapeHtml(d.nearDup.title || '') + ' · score '
          + (d.nearDup.score != null ? d.nearDup.score.toFixed(2) : '?')
          + '</div><div style="opacity:0.75">' + escapeHtml(d.nearDup.reason || '') + '</div>';
        document.getElementById('cForce').classList.remove('hidden');
        document.getElementById('cMerge').classList.remove('hidden');
      } else {
        nearBox.classList.add('hidden');
        document.getElementById('cForce').classList.add('hidden');
        document.getElementById('cMerge').classList.add('hidden');
      }
    });
  </script>
</body>
</html>`;
  }
}
