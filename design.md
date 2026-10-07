# Design System: OmniCloud

> **Supersession note (v1):** Sections 1-3 (the custom Geist/Zinc visual system) are
> superseded by **Material Design (Material 3 via MUI)**: neutral light theme, standard
> MUI components and elevation, desaturated status colors mapped to Material's
> success/warning/error slots. Typography uses the default Material stack; the
> mono-for-numbers rule survives as a `tabular-nums` utility on all numeric cells.
> Sections 4-10 (UI contracts) remain binding: canonical entity model, capability-based
> actions, data honesty rules, credential masking, confirmation dialogs, provider as
> facet, open-source hygiene.

> **What OmniCloud is:** an open-source, multi-provider cloud control panel that aggregates
> server fleets, traffic allowances, and billing exposure across cloud providers. Fleet
> adapters: **Hetzner**, **Leaseweb**, **OVH**, **Gcore Cloud**, **Gcore Hosting**,
> **Netlen**, **Lightnode**, **Tube-hosting**.
>
> **Open-source constraint:** this document, all UI copy, screenshots, fixtures, and
> documentation must contain **no real company names, credentials, IP addresses, or
> customer data**. Example data uses placeholder identifiers only.

## 1. Visual Theme & Atmosphere

A strictly monochromatic, instrument-quiet operations dashboard. The entire chromatic
range lives on a single neutral axis — no hue, no warmth, no coolness — so hierarchy is
carried entirely by tone, weight, and space. The atmosphere is an air-traffic console in
a matte finish: dense, scannable, and calm, where the loudest thing on any screen is the
one thing that needs a decision.

- **Density:** 7 — Cockpit Dense. Tables and fleet grids carry the interface; whitespace
  is structural, not decorative.
- **Variance:** 4 — predictable grids with offset summaries. This is a tool, not a
  portfolio; scannability beats surprise.
- **Motion:** 3 — Static Restrained. Motion confirms state changes; it never entertains.

Since color is absent, **contrast is the accent**. A near-black button on a pale surface
is the loudest element on any screen, and it should be the only one. The operator's eye
should land on data first, chrome never.

## 2. Color Palette & Roles

A single neutral scale (Zinc). One step lighter or darker than its neighbor — no
warm/cool fluctuation across the system.

- **Paper White** (#FAFAFA) — Primary background surface, the page itself
- **Pure Surface** (#FFFFFF) — Content surfaces, floating one tone above Paper White
- **Fog Fill** (#F4F4F5) — Inset surfaces: input backgrounds, hover fills, meter tracks, subtle wells
- **Mist Line** (#E4E4E7) — Full-strength 1px borders, dividers, table rules
- **Whisper Border** (rgba(228, 228, 231, 0.5)) — Half-strength borders on quiet containers
- **Muted Steel** (#A1A1AA) — Placeholder text, disabled states, empty meter fills
- **Slate Mid** (#71717A) — Secondary text, descriptions, metadata, captions
- **Graphite** (#3F3F46) — High-emphasis secondary text, icon strokes at rest
- **Charcoal Ink** (#18181B) — Primary text AND the interaction accent: primary buttons, active nav, focus rings, selection, links. Off-black, never pure black
- **Void** (#09090B) — Maximum-depth surfaces only (code blocks, terminal output, tooltips)

**Accent strategy:** Monochrome — Charcoal Ink *is* the accent. A primary CTA is solid
#18181B with #FAFAFA text. Focus rings are 2px #18181B. Active nav is #18181B. There is
no chromatic brand accent, and provider brand colors are never used — an OVH server and
a Hetzner server are rendered in identical ink.

**Status semantics (the sole chromatic exception):**
Operational health is the one place where tone alone fails an operator scanning a
400-server wall for failures. Three deep, muted shades — chosen for text-grade contrast
on Paper White (all pass WCAG AA at ≥ 4.5:1) — exist only as compact status dots and
badge text, always paired with a text label, never as fills for large surfaces:

- **Running Green** (#3F6212) — healthy / reporting
- **Warning Amber** (#854D0E) — allowance above 80%, degraded, stale data
- **Down Red** (#991B1B) — failed, unreachable, overage
- (Neutral states use Muted Steel — no fourth color)

If strict grayscale is later preferred, statuses may instead be encoded by icon shape +
text only; the text-pairing rule makes either scheme accessible. But the default is:
monochrome interface, desaturated status trio, text always.

**Banned:** `#000000` (pure black), any chromatic color outside the status trio,
warm/cool grays, provider brand hues anywhere, purple/blue neon, gradient text, shadows
on text.

**Focus on dark surfaces:** the Charcoal Ink focus ring is invisible on Void surfaces.
On Void (tooltips, terminal, code blocks) the focus ring inverts to 2px Paper White.

**Dark mode (deferred):** v1 ships light-only. When added, map by symmetric Zinc steps
(Paper White ↔ #09090B, Pure Surface ↔ #18181B, Fog Fill ↔ #27272A, Mist Line ↔ #3F3F46,
text inverts) and lighten the status trio one step for contrast. No new hues enter the
system in dark mode.

## 3. Typography Rules

- **Display:** **Geist** — Track-tight (-0.02em at large sizes), controlled scale, weight
  500–600. Hierarchy through weight and tone (#18181B vs #71717A), not size. Dashboards
  whisper; they don't shout.
- **Body:** **Geist** — 400 weight, 1.5–1.6 leading, max 65 characters per line, colored
  Slate Mid so ink is reserved for data.
- **Mono:** **Geist Mono** — mandatory for **every number** (density exceeds 7): traffic
  values, allowance percentages, prices, IP addresses, server IDs, regions, timestamps,
  versions. Tabular figures on. A rate that updates must not reflow its neighbors.
- **Banned:** Inter, Roboto, system-ui as identity fonts. All serifs, without exception.
  Letter-spaced all-caps body text.

**Icons:** one system only — inline SVG, 1.5px stroke, Graphite at rest / Charcoal Ink on
hover and active, round caps and joins, drawn on a 24px grid, rendered at 16–20px.
Status dots are geometry (filled circle), not icons. Provider identity in the UI is text
(Hetzner, Leaseweb) — never a scraped or trademarked logo. When Lucide provides the glyph
use it unmodified; custom glyphs follow the same stroke grid.

**Scale:** Page title 1.5rem / section head 1.125rem / body 0.9375rem / secondary
0.8125rem / label 0.75rem uppercase, tracked +0.08em, Slate Mid. Dashboard numbers may
run larger for hero metrics only (traffic-now, fleet count) at 2–2.5rem mono. Body text
never below 0.875rem; only labels and metadata go smaller.

## 4. The Provider-Agnostic UI Contract

Every adapter (Hetzner, Leaseweb, OVH, Gcore, Netlen, Lightnode, Tube-hosting) maps its
API onto a **canonical entity model** — Server, IpAddress, Region, Image, Allowance,
Billing, Invoice — and
the UI renders only canonical entities. The canonical model is defined once, in code, by
a typed schema both adapters implement; if a provider offers no equivalent for a field,
the adapter omits it (rendering then follows §6's unavailability rules). One design
consequence:

- **No provider-specific components.** If Hetzner has "floating IPs" and Leaseweb has
  "private networks," they surface through a generic labeled facets/attachments list, not
  bespoke panels. New provider = zero new UI patterns.
- **Capability-based actions.** Actions (power, rebuild, snapshot, rescue mode) render
  from a capability set the adapter declares. A capability the adapter lacks is **absent
  from the UI**, not a disabled button — absent capabilities are not failures.
- **Provider is a facet, not an identity.** Filter and group by provider in the fleet
  list; never brand a card with provider colors or logos.
- **Allowance normalization.** Traffic allowances differ per provider (e.g. some count
  ingress, some egress, some both; windows reset monthly or on a rolling window). The
  allowance meter always shows: bytes used, bytes included, window definition in plain
  language, and estimated overage cost — all from adapter-provided canonical fields. The
  UI never guesses a provider's billing model.

## 5. Layout Principles

- **App shell:** fixed left navigation rail (240px, collapsible to icon rail 64px) with
  grouped routes — Fleet, Allowances & billing, Credentials, Adapters, Settings. Content
  offset matches the rail; compact top bar (56px) holds global search and refresh.
- **Fleet first.** The default view is a dense server grid/table: status dot, alias,
  region, provider, allowance meter, traffic rates, and the one action that matters.
  A server the panel cannot reach is never omitted — its row states the reason, because
  a machine missing from the list is the machine nobody fixes.
- **Summary strips** above content: 6–8 compact stat tiles (fleet size, reporting /
  not-reporting counts, traffic now, allowance headroom, projected overage cost) in mono,
  each with a text label. Unequal widths where one metric dominates (traffic now).
- Grid-first architecture; CSS Grid over Flexbox math, no `calc()` percentage hacks.
- Max content width 1600px; internal padding `clamp(1rem, 2.5vw, 2rem)`.
- Rows over cards at density 7: 1px Mist Line dividers, not floating cards. Cards are
  reserved for the summary strip and detail dialogs.
- Detail views open as native `<dialog>` overlays (meter detail, per-filesystem usage,
  action confirmation) — never full-page navigation for a look-but-don't-stay task.
- Every element owns a clean spatial zone — no overlaps, no absolute-stacked content.
- Destructive actions (rebuild, delete) always confirm in a dialog that names the server
  and states irreversibility in plain text.
- Anything that spends money (IP add/change, executing a real order, enabling purchases
  on an account) confirms the same way — typed confirmation, the cost in the provider's
  own billing unit ("per hour while it exists", "per month", "per purchase"), or
  "price not published" when the provider doesn't say. A server's primary IP has no
  change or release control at all.

## 6. Data Honesty Rules

Inherited hard requirements from the operating context this tool serves — treat as
non-negotiable:

- **Never invent a measurement.** Distinguish the two unavailability cases the operating
  context already taught: a value the provider's API does not expose reads
  **"not exposed"** in Slate Mid; a measurable value that is momentarily missing (source
  offline, sync interval not yet reached) reads **"—"** with a tooltip explaining what
  is pending. Neither is ever zero, and neither ever gets a cosmetically filled meter.
- **Missing ≠ zero.** A server that isn't reporting shows a "not reporting" state with
  the last-known timestamp — not 0 bytes, which would read as a healthy idle server.
- **Every number is labeled with its window** ("out this month", "last 24 h") and its
  source (adapter-reported vs panel-measured).
- **State changes show their intermediate state.** An API action in flight shows
  progress and what step it's on; a `202` accepted response is never rendered as success
  — the panel confirms the provider's own view before declaring an action done.
- **Stale data decays visibly.** Values older than their source's expected interval dim
  to Slate Mid with an "as of HH:MM" stamp rather than presenting as live.
- **Refresh is explicit, sync is continuous.** The top-bar Refresh button forces a
  provider re-fetch now. Live values (traffic rates) arrive over an authenticated
  WebSocket/stream and swap in place with no counter animation. Each data source keeps
  its own timestamp; one slow provider never freezes or blocks another's sections.
  Polling intervals live in Settings (visible, editable), not hidden constants.

## 7. Component Stylings

- **Buttons:** Flat fill, zero glow. Primary: Charcoal Ink fill, Paper White text, 0.5rem
  radius. Secondary: 1px Mist Line border, Graphite text. Ghost: text-only, underline on
  hover. All buttons translate -1px on active, 120ms spring. One primary action per
  view; row-level actions are ghost icon buttons (44px target) with visible labels on
  hover/keyboard focus.
- **Tables:** Header in label style, rows 1px Whisper Border rules, cell padding
  12–16px. All quantities in Geist Mono, right-aligned; identifiers left-aligned.
  Tables scroll inside their own container; they never widen the page. Sort and filter
  controls sit in the page heading, not inside the table.
- **Status badges:** compact dot + text (e.g. `● Reporting`), small radius, from the
  status trio with mandatory text pairing. Never a bare colored dot.
- **Meters:** 6px track in Fog Fill, fill in Charcoal Ink; fill turns Warning Amber
  above 80% of allowance and Down Red above 95% or on overage — thresholds match the
  status definitions in §2 exactly. Every meter has its number adjacent, in mono —
  meters never carry data alone.
- **Inputs:** Label above the field, always. Fill Fog Fill, no resting border, 1px
  Charcoal Ink border on focus. Error text below in Down Red with an "Error —" prefix.
  No floating labels.
- **Credential fields (API tokens, panel logins):** each adapter declares its own form
  (one token, or URL + username + password for panels without API tokens). Secret
  fields are masked by default with an explicit reveal toggle that changes its
  accessible name. Secret values never appear in URLs, logs, table cells, dialogs, or
  screenshots. The credentials page shows provider account, the last 4 characters of
  the token or login name (never of a password), scope, added-date, last-used.
- **Loaders:** skeleton rows matching the exact table/grid rhythm in Fog Fill with a
  slow shimmer. No circular spinners anywhere.
- **Empty states:** one composed mark (Geist Mono glyph), one line of explanation, one
  action — e.g. "No servers connected. Connect a provider account to import its fleet."
- **Focus:** 2px Charcoal Ink ring, 2px offset — visible on every light surface. On
  dark-filled controls (primary buttons) the ring inverts to 2px Paper White, outside
  the fill. Focus is never removed; `:focus-visible` styling only, so pointer clicks
  stay clean but keyboard users always get the ring.
- **Sparklines:** 1.5px Graphite stroke, no fill area, Fog Fill band for the trailing
  range, on a fixed 28px height. An empty sparkline renders no line — never a flat
  baseline at zero, which would read as a measured zero.
- **Tooltips/dialogs:** Void surface, Paper White text, mono for any value they cite.

## 8. Responsive Rules

- Below 768px: rail collapses to a bottom-anchored or hamburger menu; every grid
  collapses to a single column; tables switch to stacked rows or a horizontal-scroll
  container with sticky first column. No page-level horizontal scroll.
- Summary strips wrap 2-per-row on phones, keeping labels visible.
- Body minimum 0.875rem; all touch targets 44×44px.
- Meters and sparklines keep fixed heights so reflow never jiggles rows.

## 9. Motion & Interaction

- 150–200ms, `cubic-bezier(0.2, 0, 0, 1)` for state changes. Nothing bounces.
- Live values (traffic rates) update in place with no counter animation — numbers swap,
  they never roll or interpolate. Invented intermediate readings are banned.
- Lists stagger-reveal once on mount (30ms/item, cap 8) — no perpetual loops on fleet
  surfaces. The only infinite states are skeleton shimmers and a 2px indeterminate
  progress rule on in-flight API actions.
- Animate `transform` and `opacity` only.
- `prefers-reduced-motion`: all non-essential transitions disabled.

## 10. Anti-Patterns (Banned)

*(One runtime caveat that belongs here rather than in a design doc: map and network
topology views — if ever built — must not embed map tiles or geo assets that phone home
or carry provider-identifying metadata. For an open-source ops tool, a pure-SVG abstract
region diagram is the safe default.)*

- No emojis anywhere in the interface.
- No Inter/Roboto; no serifs; no system font as the identity face.
- No pure black `#000000` — Charcoal Ink is the darkest value.
- No chromatic brand accent; no provider brand colors or logos in the UI.
- No status color without a text label; no large chromatic surfaces.
- No warm/cool gray mixing — the entire system stays on the Zinc axis.
- No neon, no outer glows, no gradient text, no shadows on text.
- No circular loading spinners; skeletons only.
- No invented measurements, no cosmetic zeros, no "not exposed" and "—" used
  interchangeably — they mean different things (§6).
- No secret ever rendered: API tokens masked, never in URLs, logs, or screenshots.
- No real company data in any screenshot, fixture, or example — placeholder names
  (`srv-fsn1-01`, `account-a7f3`) and `picsum.photos`/inline SVG only.
- No generic filler names ("John Doe", "Acme"); no fake round numbers ("99.99%").
- No AI copywriting clichés: "Elevate", "Seamless", "Unleash", "Next-Gen".
- No custom mouse cursors; no filler UI text ("Scroll to explore"); no 3-equal-card
  marketing rows — this is a console, not a landing page.
