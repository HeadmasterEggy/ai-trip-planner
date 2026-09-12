"""Presentation tokens and the rendering CSS.

The palette, spacing rhythm and card shapes are carried over from the React
implementation so the two front ends look like the same product. Streamlit
cannot express all of it — there is no per-component stylesheet — so this is
one injected block scoped by class name.

The palette lives in two files and that is deliberate: `.streamlit/config.toml`
is the only place Streamlit's own widgets read colours from, and this module owns
everything we draw ourselves. `PALETTE` repeats the five values they share, and
`tests/test_theme.py` parses the TOML and fails when the two drift apart — a
palette in two files drifts silently otherwise.

There is one palette, and the app pins it: no light/dark switch. Streamlit
resolves a theme from the app's config *and* the user's own preference, and
pinning anything at the top level of `[theme]` is what makes the app's config
win — which is also why Streamlit hides its own theme picker in this app. See
entry 13 of `docs/debugging-log.md` for what that cost to establish.
"""

from __future__ import annotations

# Named for what they are for, not for what colour they happen to be, so a
# re-theme is a swap of this map and nothing else. The five marked `[theme]`
# repeat `.streamlit/config.toml`; `tests/test_theme.py` pins them, because a
# palette written down twice drifts silently otherwise.
#
# `surface` is the rail, and it has to equal Streamlit's own secondary
# background: the components that sit on the rail -- the sticky `New chat`
# gradient, a hovered row -- blend against it.
PALETTE = {
    # Page and text.
    "bg": "#FBFBFC",  # [theme] backgroundColor
    "surface": "#F4F4F6",  # [theme] secondaryBackgroundColor: the rail
    "surface-2": "#E9ECF1",  # raised above the rail: cards, rows, hover
    "border": "#E5E7EB",  # [theme] borderColor
    "text": "#18181B",  # [theme] textColor
    "text-dim": "#5A6072",
    "text-mut": "#808495",
    # Accent.
    "accent": "#4F46E5",  # [theme] primaryColor
    "accent-bg": "#EEF2FF",
    # Text on a filled accent/amber badge.
    "on-accent": "#FFFFFF",
    "on-warn": "#3A2A05",
    # Semantic. Amber is the one loud badge, and it keeps dark text.
    "ok": "#047857",
    "ok-bg": "#ECFDF5",
    "warn": "#B45309",
    "warn-strong": "#F59E0B",
    "warn-bg": "#FFFBEB",
    # Timeline owners.
    "transport": "#0369A1",
    "accommodation": "#7C3AED",
    "dining": "#B45309",
    # Trip-card covers. There is no photo source, so a card is a gradient from
    # one of these into `cover-deep`, picked by `history.cover_index`.
    "cover-1": "#2563EB",
    "cover-2": "#0891B2",
    "cover-3": "#059669",
    "cover-4": "#D97706",
    "cover-5": "#DB2777",
    "cover-6": "#7C3AED",
    "cover-deep": "#1E1B4B",
}

RADII = {"radius-sm": "6px", "radius-md": "14px", "radius-pill": "999px"}

COVERS = sum(1 for name in PALETTE if name.startswith("cover-") and name != "cover-deep")


def _root() -> str:
    """The `:root` custom properties, generated from the two maps above."""
    lines = [f"  --tp-{name}: {value};" for name, value in {**PALETTE, **RADII}.items()]
    return "\n".join(lines)


def _covers() -> str:
    """One modifier class per cover slot, so the card markup names a slot, not a colour."""
    return "\n".join(
        f".tp-trip-card--{n} .tp-trip-card__cover {{ background: linear-gradient("
        f"135deg, var(--tp-cover-{n + 1}), var(--tp-cover-deep)); }}"
        for n in range(COVERS)
    )


CSS = (
    """
<style>
:root {
"""
    + _root()
    + """
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
.tp-chip--needs_you { background: var(--tp-warn-strong); color: var(--tp-on-warn); }
.tp-chip--confirmed { background: var(--tp-ok-bg); color: var(--tp-ok); }
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

/* The name beside the mark. `st.logo` takes an image and nothing else, and an
   <img> cannot carry ::after, so the wordmark is drawn by the logo's wrapper --
   in the rail header when the rail is open, in the page header when it is not --
   and the two never disagree. Without a `link=` there is no stLogoLink to use. */
:has(> [data-testid="stSidebarLogo"]),
:has(> [data-testid="stHeaderLogo"]) {
  display: inline-flex !important;
  align-items: center;
  gap: 10px;
}
:has(> [data-testid="stSidebarLogo"])::after,
:has(> [data-testid="stHeaderLogo"])::after {
  content: "AI Trip Planner";
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
  color: var(--tp-accent);
}

/* ---------------------------------------------------------------------------
   The rail. Rows are navigation, not form controls: left-aligned, quiet until
   hovered. Scoped to their container key so the trip form's own buttons keep
   Streamlit's styling -- and so the only thing a new row has to do is live in
   the right container.
   --------------------------------------------------------------------------- */
.st-key-rail-search input {
  background: var(--tp-surface-2) !important;
  border: 1px solid var(--tp-border) !important;
  border-radius: var(--tp-radius-pill) !important;
  color: var(--tp-text) !important;
}
.st-key-rail-search input::placeholder { color: var(--tp-text-mut) !important; }

.tp-rail__section {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--tp-text-mut);
  margin: 16px 0 2px;
}
.tp-rail__badge {
  margin-left: auto; font-size: 10px; font-weight: 600; letter-spacing: 0;
  background: var(--tp-surface-2); color: var(--tp-text-dim);
  border-radius: var(--tp-radius-pill); padding: 1px 7px;
}
.tp-rail__empty { color: var(--tp-text-mut); font-size: 12px; padding: 2px 0; }

/* The panels are expanders, but they sit in a list of nav rows. Streamlit draws
   an expander as a bordered box with a chevron; inside the rail it loses the box
   so it matches the rows around it, and keeps the chevron because it does open. */
.st-key-rail-nav [data-testid="stExpander"] details,
.st-key-rail-nav [data-testid="stExpander"] > details {
  border: none !important;
  background: transparent !important;
}
.st-key-rail-nav [data-testid="stExpander"] summary {
  font-size: 14px; font-weight: 500; color: var(--tp-text);
  padding: 8px 9px !important; border-radius: var(--tp-radius-sm);
}
.st-key-rail-nav [data-testid="stExpander"] summary:hover {
  background: var(--tp-surface-2);
}

/* The conversation you are in cannot be a button -- it is already open -- so it
   is drawn as the row the list uses to say "you are here". */
.tp-rail__active {
  display: flex; align-items: center; gap: 8px;
  border-left: 2px solid var(--tp-accent);
  background: var(--tp-surface-2);
  border-radius: 0 var(--tp-radius-sm) var(--tp-radius-sm) 0;
  padding: 7px 9px; margin: 2px 0;
  font-size: 13px; font-weight: 600;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}

.st-key-rail-nav button,
.st-key-rail-history button {
  justify-content: flex-start !important;
  text-align: left !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  color: var(--tp-text) !important;
  font-weight: 500 !important;
  padding: 7px 9px !important;
  min-height: 0 !important;
  line-height: 1.3 !important;
  width: 100% !important;
}
.st-key-rail-nav button:hover,
.st-key-rail-history button:hover {
  background: var(--tp-surface-2) !important;
  color: var(--tp-text) !important;
}
/* One line per row, ellipsised: a rail that reflows with every title cannot be
   scanned. */
.st-key-rail-nav button p,
.st-key-rail-history button p {
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  font-size: 13px; width: 100%; text-align: left;
}
/* A row's subtitle belongs to the row above it, so it is tighter than a normal
   caption and indented to the label. */
.st-key-rail-history [data-testid="stCaptionContainer"] {
  margin: -4px 0 4px 9px;
}
.st-key-rail-history [data-testid="stCaptionContainer"] p { font-size: 11px; }

/* Pinned to the bottom of the rail, like the reference: the primary action has
   to be reachable without scrolling back up a long history. The gradient fades
   to the rail's own colour -- fading to the page colour would leave a smudge --
   which is what keeps rows from showing through it. */
.st-key-rail-new-chat {
  position: sticky;
  bottom: 0;
  z-index: 6;
  padding-top: 10px;
  background: linear-gradient(180deg, transparent, var(--tp-surface) 45%);
}
.st-key-rail-new-chat button {
  width: 100% !important;
  border-radius: var(--tp-radius-pill) !important;
  font-weight: 600 !important;
}

/* The opening screen. Before anything is planned there is no plan to put beside
   the conversation, so this is the whole page: a greeting, and the invitation to
   say where. Centred vertically as well as horizontally, because the greeting is
   what the traveller should be looking at, not the top of an empty panel. An app
   that opens on a form has already asked the traveller to do the work. */
.tp-hero {
  text-align: center;
  padding: 15vh 0 20px;
}
.tp-hero__mark { font-size: 42px; line-height: 1; }
.tp-hero__title {
  font-size: 34px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 12px 0 8px;
}
.tp-hero__sub {
  color: var(--tp-text-dim);
  font-size: 14px;
  line-height: 1.5;
  max-width: 460px;
  margin: 0 auto;
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
.tp-tl__row--transport { border-left-color: var(--tp-transport); }
.tp-tl__row--itinerary { border-left-color: var(--tp-accent); }
.tp-tl__row--accommodation { border-left-color: var(--tp-accommodation); }
.tp-tl__row--dining { border-left-color: var(--tp-dining); }
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
  background: var(--tp-surface-2);
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
  background: var(--tp-accent); color: var(--tp-on-accent); font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.tp-step__n--done { background: var(--tp-ok); }
.tp-step__n--todo { background: var(--tp-warn-strong); color: var(--tp-on-warn); }
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
.tp-src--calc { background: var(--tp-ok-bg); color: var(--tp-ok); }
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

/* Chats / Trips. The page you are on is drawn the way the list draws the open
   conversation: an accent edge on a raised row. */
[class*="st-key-navrow-"][class*="-on"] button {
  background: var(--tp-surface-2) !important;
  box-shadow: inset 2px 0 0 var(--tp-accent) !important;
  font-weight: 600 !important;
}
[class*="st-key-navrow-"] button p { font-size: 14px !important; }

/* Your trips. A title with its one action, then a grid of cards. */
.tp-trips__title { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }
.tp-trips__section {
  font-size: 14px; font-weight: 600; color: var(--tp-text-dim);
  margin: 12px 0 10px;
}
.tp-trips__empty { color: var(--tp-text-mut); font-size: 14px; padding: 24px 0; }
.st-key-trips-new button { border-radius: var(--tp-radius-pill) !important; }

.tp-trip-card__cover {
  position: relative;
  aspect-ratio: 4 / 3;
  border-radius: var(--tp-radius-md);
  overflow: hidden;
  color: var(--tp-on-accent);
}
.tp-trip-card__mark {
  position: absolute; top: 14px; right: 16px; font-size: 34px;
}
.tp-trip-card__text { position: absolute; left: 16px; bottom: 14px; right: 16px; }
.tp-trip-card__title {
  font-size: 17px; font-weight: 700;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.tp-trip-card__meta { font-size: 12px; opacity: 0.85; margin-top: 2px; }
[class*="st-key-trip-card-"] button {
  width: 100% !important;
  border-radius: var(--tp-radius-pill) !important;
  margin-top: -4px;
}
"""
    + _covers()
    + """
</style>
"""
)

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
