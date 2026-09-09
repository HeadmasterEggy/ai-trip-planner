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
