/**
 * Phase 1 — Agent State Indicator mapping (Contemplating pill).
 * See docs/app/z-agent-state-trace-plan.md
 */

export type IndicatorIcon = "sunburst" | "magnifier";

export type IndicatorStateId =
  | "contemplating"
  | "editing"
  | "searching"
  | "searching_web"
  | "reading"
  | "running"
  | "working";

export type StateIndicator =
  | { visible: false }
  | {
      visible: true;
      stateId: IndicatorStateId;
      label: string;
      icon: IndicatorIcon;
    };

export type IndicatorActivityPhase =
  | "idle"
  | "thinking"
  | "planning"
  | "editing"
  | "searching"
  | "running"
  | "mcp"
  | "choosing_model"
  | "waiting"
  | "queued";

export interface DeriveIndicatorInput {
  live: boolean;
  phase: IndicatorActivityPhase;
  /** True while assistant answer tokens are actively arriving. */
  answerStreaming: boolean;
  /** User-facing waiting prompt is open (waiting UI owns the moment). */
  waitingForUser: boolean;
  busyLabel?: string;
  exploredFiles?: number;
}

const HIDDEN: StateIndicator = { visible: false };

function looksLikeWebSearch(label: string): boolean {
  const t = (label || "").toLowerCase();
  return (
    t.includes("web search") ||
    t.includes("searching the web") ||
    t.includes("search_web") ||
    t.includes("brave") ||
    t.includes("bing") ||
    t.includes("duckduck") ||
    (/\bweb\b/.test(t) && t.includes("search"))
  );
}

/**
 * Map activity strip phase → ambient Contemplating-row state.
 * Hidden when idle, waiting on the user, or answer text is streaming.
 */
export function deriveStateIndicator(input: DeriveIndicatorInput): StateIndicator {
  if (!input.live) {
    return HIDDEN;
  }
  if (input.waitingForUser || input.phase === "waiting") {
    return HIDDEN;
  }
  if (input.answerStreaming) {
    return HIDDEN;
  }
  if (input.phase === "idle" || input.phase === "queued") {
    return HIDDEN;
  }

  const label = input.busyLabel || "";

  switch (input.phase) {
    case "editing":
      return { visible: true, stateId: "editing", label: "Editing", icon: "sunburst" };
    case "searching":
      if (looksLikeWebSearch(label)) {
        return {
          visible: true,
          stateId: "searching_web",
          label: "Searching the web",
          icon: "magnifier",
        };
      }
      return { visible: true, stateId: "searching", label: "Searching", icon: "sunburst" };
    case "running":
      return { visible: true, stateId: "running", label: "Running", icon: "sunburst" };
    case "mcp":
      return { visible: true, stateId: "working", label: "Working", icon: "sunburst" };
    case "thinking":
    case "planning":
    case "choosing_model":
      // Prefer Reading when the live signal is file exploration without edits/searches.
      if (
        input.phase === "thinking" &&
        (input.exploredFiles || 0) > 0 &&
        /read|explor|open file|glob/i.test(label)
      ) {
        return { visible: true, stateId: "reading", label: "Reading", icon: "sunburst" };
      }
      if (/read|explor|open file|glob/i.test(label)) {
        return { visible: true, stateId: "reading", label: "Reading", icon: "sunburst" };
      }
      return {
        visible: true,
        stateId: "contemplating",
        label: "Contemplating",
        icon: "sunburst",
      };
    default:
      return {
        visible: true,
        stateId: "contemplating",
        label: "Contemplating",
        icon: "sunburst",
      };
  }
}
