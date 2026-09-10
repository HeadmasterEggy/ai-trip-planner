"""Presentation tokens and the rendering CSS.

The palette, spacing rhythm and card shapes are carried over from the React
implementation so the two front ends look like the same product. Streamlit
cannot express all of it — there is no per-component stylesheet — so this is
one injected block scoped by class name.
"""

from __future__ import annotations

# Kept in the same order and naming as the original design tokens so a change
# in one front end is easy to mirror in the other.
CSS = """
<style>
:root {
  --tp-surface-2: #f4f4f6;
  --tp-text-dim: #6b7280;
  --tp-text-mut: #9ca3af;
  --tp-border: #e5e7eb;
  --tp-accent: #4f46e5;
  --tp-accent-bg: #eef2ff;
  --tp-ok: #047857;
  --tp-warn: #b45309;
  --tp-warn-strong: #f59e0b;
  --tp-warn-bg: #fffbeb;
  --tp-radius-sm: 6px;
  --tp-radius-pill: 999px;
}

/* Proposal card: an accent edge keeps a long list scannable, which is the
   whole reason the expanded section is worth opening. */
.tp-item {
  border: 1px solid var(--tp-border);
  border-left: 3px solid var(--tp-accent);
  border-radius: var(--tp-radius-sm);
  background: var(--tp-surface-2);
  padding: 10px 12px;
  margin-bottom: 8px;
}
.tp-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 10px;
  align-items: center;
  color: var(--tp-text-dim);
  font-size: 11px;
  margin-bottom: 4px;
}
.tp-item__kind {
  text-transform: capitalize;
  color: var(--tp-accent);
  font-weight: 600;
}
.tp-item__cost {
  margin-left: auto;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: inherit;
}
.tp-item__title { font-size: 13px; font-weight: 600; margin: 2px 0; }
.tp-item__detail { color: var(--tp-text-dim); font-size: 12px; margin: 0; }

/* Status chips mirror the section statuses the orchestrator produces. */
.tp-chip {
  display: inline-block;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 9px;
  border-radius: var(--tp-radius-pill);
  white-space: nowrap;
}
.tp-chip--draft { background: var(--tp-surface-2); color: var(--tp-text-mut); }
.tp-chip--needs_you { background: var(--tp-warn-strong); color: #3a2a05; }
.tp-chip--confirmed { background: #ecfdf5; color: var(--tp-ok); }
.tp-chip--planning { background: var(--tp-accent-bg); color: var(--tp-accent); }

/* Agent activity rows. Only the row actually working moves. */
.tp-agent {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
}
.tp-agent__status { margin-left: auto; font-size: 12px; }
.tp-agent__status--running { color: var(--tp-accent); }
.tp-agent__status--completed { color: var(--tp-ok); }
.tp-agent__status--failed { color: var(--tp-warn); }
.tp-agent__status--queued { color: var(--tp-text-mut); }
.tp-dot {
  width: 6px; height: 6px; border-radius: var(--tp-radius-pill);
  background: currentColor; display: inline-block;
  animation: tp-pulse 1.2s ease-in-out infinite;
}
@keyframes tp-pulse { 50% { opacity: 0.25; } }

/* Budget: over budget must not look like on budget. The bar pins to 100% in
   both cases, so colour is the only signal left. */
.tp-budget { display: flex; justify-content: space-between; font-size: 13px; }
.tp-budget__total { color: var(--tp-text-mut); }
.tp-bar { height: 8px; border-radius: var(--tp-radius-pill); background: var(--tp-surface-2); overflow: hidden; }
.tp-bar > span { display: block; height: 100%; border-radius: inherit; background: var(--tp-ok); }
.tp-bar--over > span { background: var(--tp-warn-strong); }
.tp-delta { font-size: 12px; margin: 6px 0 4px; color: var(--tp-ok); }
.tp-delta--over { color: var(--tp-warn); }

.tp-note { color: var(--tp-text-mut); font-size: 12px; }

/* The plan rail's handle.
   Targeted by Streamlit's own `st-key-<widget key>` class rather than by DOM
   adjacency: the button sits several wrappers deep and gains another when it
   has a tooltip, so a sibling selector silently stops matching.
   Sticky because a rail control that scrolls away with the content cannot be
   used to bring the rail back -- collapsed, it was the only way to reopen the
   panel and it sat 240px above the viewport. */
.st-key-toggle-plan {
  position: sticky;
  top: 8px;
  z-index: 5;
}
.st-key-toggle-plan button {
  border: 1px solid var(--tp-border) !important;
  background: var(--tp-surface-2) !important;
  color: var(--tp-text-dim) !important;
  padding: 2px 8px !important;
  min-height: 0 !important;
  line-height: 1.4 !important;
}
.st-key-toggle-plan button:hover {
  border-color: var(--tp-accent) !important;
  color: var(--tp-accent) !important;
}

.tp-brand {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--tp-accent);
  margin-bottom: 2px;
}

/* Day timeline. Items from different specialists share one axis, which is the
   only way a clash between them is visible to a reader. */
.tp-tl__day { margin-bottom: 14px; }
.tp-tl__head {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--tp-text-mut); font-weight: 600; margin-bottom: 6px;
}
.tp-tl__row {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 7px 10px; margin-bottom: 4px;
  border-left: 3px solid var(--tp-border);
  background: var(--tp-surface-2);
  border-radius: 0 var(--tp-radius-sm) var(--tp-radius-sm) 0;
  font-size: 12px;
}
/* Colour by owner so transport reads as fixed and activities as movable. */
.tp-tl__row--transport { border-left-color: #0369a1; }
.tp-tl__row--itinerary { border-left-color: var(--tp-accent); }
.tp-tl__row--accommodation { border-left-color: #7c3aed; }
.tp-tl__row--dining { border-left-color: #b45309; }
.tp-tl__when {
  min-width: 84px; font-variant-numeric: tabular-nums; color: var(--tp-text-dim);
}
.tp-tl__what { flex: 1; min-width: 0; }
.tp-tl__cost { font-variant-numeric: tabular-nums; font-weight: 600; }
/* The owner as text, so the row still says who it belongs to without its colour. */
.tp-tl__owner {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--tp-text-mut); margin-left: 6px;
}

/* Budget breakdown: a single total says a plan is over; this says who by. */
.tp-bd__row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 12px; }
.tp-bd__label { min-width: 120px; color: var(--tp-text-dim); }
.tp-bd__track {
  flex: 1; height: 8px; background: var(--tp-surface-2);
  border-radius: var(--tp-radius-pill); overflow: hidden;
}
.tp-bd__track > span { display: block; height: 100%; background: var(--tp-accent); border-radius: inherit; }
.tp-bd__value { min-width: 96px; text-align: right; font-variant-numeric: tabular-nums; }

/* Inline choice cards: the decision happens where it is explained, rather than
   sending the traveller somewhere else to make it. */
.tp-opt {
  border: 1px solid var(--tp-border);
  border-radius: var(--tp-radius-sm);
  padding: 9px 11px;
  margin-bottom: 6px;
  background: #fff;
}
.tp-opt--picked { border-color: var(--tp-accent); background: var(--tp-accent-bg); }
.tp-opt__head { display: flex; align-items: baseline; gap: 8px; font-size: 13px; }
.tp-opt__name { font-weight: 600; }
.tp-opt__cost { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 600; }
.tp-opt__detail { color: var(--tp-text-dim); font-size: 11px; margin: 3px 0 0; }
.tp-opt__flag {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--tp-accent); font-weight: 600;
}

/* Numbered plan steps, mirroring the order a traveller settles them in. */
.tp-step { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 4px; }
.tp-step__n {
  flex: none; width: 20px; height: 20px; border-radius: var(--tp-radius-pill);
  background: var(--tp-accent); color: #fff; font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.tp-step__n--done { background: var(--tp-ok); }
.tp-step__n--todo { background: var(--tp-warn-strong); color: #3a2a05; }
.tp-step__body { flex: 1; min-width: 0; }

/* Specialist reasoning: what it read, and which path it took. */
.tp-trace {
  border: 1px solid var(--tp-border); border-radius: var(--tp-radius-sm);
  padding: 9px 11px; margin-bottom: 8px; font-size: 12px;
}
.tp-trace__head {
  display: flex; align-items: center; gap: 8px;
  font-weight: 600; margin-bottom: 6px;
}
.tp-src {
  margin-left: auto; font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em;
  padding: 1px 7px; border-radius: var(--tp-radius-pill);
}
.tp-src--model { background: var(--tp-accent-bg); color: var(--tp-accent); }
.tp-src--calc { background: #ecfdf5; color: var(--tp-ok); }
.tp-src--fallback { background: var(--tp-warn-bg); color: var(--tp-warn); }
.tp-ev { display: flex; gap: 10px; padding: 2px 0; align-items: baseline; }
.tp-ev__k { min-width: 130px; color: var(--tp-text-mut); flex: none; }
.tp-ev__v { flex: 1; min-width: 0; word-break: break-word; }
.tp-fallback { color: var(--tp-warn); margin: 6px 0 0; font-size: 11px; }

/* Negotiation rounds. */
.tp-rd {
  border: 1px solid var(--tp-border); border-radius: var(--tp-radius-sm);
  padding: 10px 12px; margin-bottom: 8px;
}
.tp-rd--clear { border-left: 3px solid var(--tp-ok); }
.tp-rd__head { font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.tp-rd__item { border-top: 1px solid var(--tp-border); padding-top: 8px; margin-top: 8px; font-size: 12px; }
.tp-rd__item:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
.tp-rd__item ul { margin: 4px 0 0; padding-left: 18px; color: var(--tp-text-dim); }

@media (prefers-reduced-motion: reduce) {
  .tp-dot { animation: none; }
}
</style>
"""

AGENT_ICONS = {
    "queued": "⏳",
    "agent_started": "🔄",
    "agent_completed": "✅",
    "agent_failed": "⚠️",
}
AGENT_STATUS_TEXT = {
    "queued": "Queued",
    "agent_started": "Running",
    "agent_completed": "Complete",
    "agent_failed": "Needs attention",
}
STATUS_LABEL = {
    "planning": "planning",
    "draft": "draft",
    "needs_you": "needs you",
    "confirmed": "confirmed",
}
