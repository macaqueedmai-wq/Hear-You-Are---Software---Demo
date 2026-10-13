# MoodHaven Journal — Design System

> **Scope:** Website (`website/`) and desktop app (`src/`). Both share the same color tokens and motion principles. Component implementations differ but the visual language is unified.

---

## Color Tokens

### Primary (Violet)

Tailwind scale defined in `website/tailwind.config.js` and `tailwind.config.js` (app root).

| Token | Hex | Use |
|-------|-----|-----|
| `primary-50` | `#f5f3ff` | Tinted backgrounds, callout sections |
| `primary-100` | `#ede9fe` | Hover states on light surfaces |
| `primary-200` | `#ddd6fe` | Borders, dividers on light background |
| `primary-300` | `#c4b5fd` | Muted text on dark hero |
| `primary-400` | `#a78bfa` | Icon fills, decorative |
| `primary-500` | `#8b5cf6` | Feature icons, secondary highlights |
| `primary-600` | `#7c3aed` | Active nav underlines, focus rings |
| `primary-700` | `#6d28d9` | Primary text links, active states, nav |
| `primary-800` | `#5b21b6` | — |
| `primary-900` | `#4c1d95` | Hero gradient start |
| `primary-950` | `#2e1065` | Deep shadow tints |

### Accent

| Token | Hex | Use |
|-------|-----|-----|
| `accent-cta` | `#F28C38` | Primary CTA buttons ("Open in Browser", "Try Free") |
| `accent-ctaDecor` | `#F28C38` | Decorative accent (same value, semantic alias) |

### Mood Scale

Used in app charts, calendar heatmap, and mood selector. **Do not use in website UI.**

| Token | Hex | Mood level |
|-------|-----|-----------|
| `mood-struggling` | `#f43f5e` | 1 — Struggling |
| `mood-low` | `#fb923c` | 2 — Low |
| `mood-okay` | `#fbbf24` | 3 — Okay |
| `mood-good` | `#a3e635` | 4 — Good |
| `mood-great` | `#10b981` | 5 — Great |

### Neutral / Surface

Tailwind defaults. Key usage:

| Value | Use |
|-------|-----|
| `#F3F0EA` | Page background (warm off-white) — set on `<body>` |
| `white` | Card and container surfaces |
| `neutral-200` | Borders, dividers |
| `neutral-500` | Secondary text, captions |
| `neutral-900` | Primary body text |

---

## Typography

Font: **Inter** (Google Fonts, `next/font/google`, `display: swap`). CSS variable: `--font-inter`.

| Role | Size | Weight | Notes |
|------|------|--------|-------|
| Hero H1 | `text-3xl` / `md:text-5xl` | `font-bold` | `tracking-tight` |
| Section H2 | `text-xl` | `font-bold` | Page sections |
| Card H3 | `text-sm` | `font-semibold` | Feature cards, callout items |
| Body | `text-sm` | `font-normal` | `leading-relaxed` |
| Caption / label | `text-xs` | `font-medium` / normal | Subtext, nav labels, badges |
| Section eyebrow | `text-sm` | `font-semibold` | `uppercase tracking-widest text-neutral-500` |

---

## Spacing & Layout

- **Max content width:** `max-w-5xl` (nav, footer, most sections) / `max-w-4xl` (hero copy) / `max-w-3xl` (long-form pages: FAQ, Privacy, Blog)
- **Page shell:** `max-w-5xl bg-white rounded-3xl shadow-xl` — the card container in `layout.tsx`
- **Section padding:** `py-12` – `py-14` vertical, `px-4` horizontal
- **Card padding:** `p-4` – `p-6` inner

---

## Motion

All entrance animations use **`AnimatedReveal`** (`components/AnimatedReveal.tsx`):

```
initial:    { opacity: 0, y: 20 }
whileInView: { opacity: 1, y: 0 }
viewport:   { once: true, amount: 0.4 }
transition: { duration: 0.5, delay }
```

- Staggered children: `delay={i * 0.2}` (feature grid) or `delay={i * 0.1}` (callout items)
- Micro-interactions: `hover:scale-105` on CTA buttons, `hover:scale-[1.015]` on cards
- Duration: `duration-200` for micro-interactions, `duration-300` for nav/drawer transitions
- Scroll-to-top button: Framer `AnimatePresence` with `y: 20` enter/exit

**Respect `prefers-reduced-motion`:** Framer Motion honors this automatically via `useReducedMotion`. No additional CSS needed.

---

## Component Vocabulary

### Buttons

| Variant | Classes | Use |
|---------|---------|-----|
| Primary CTA | `rounded-full bg-accent-cta text-neutral-900 px-6 py-3.5 text-sm font-semibold shadow hover:bg-accent-cta/90 hover:scale-105` | "Open in Browser", "Try Free" |
| Secondary CTA | `rounded-full bg-white text-primary-700 px-6 py-3.5 text-sm font-semibold shadow hover:bg-primary-100 hover:scale-105` | "Download for Desktop" |
| Ghost / outline | `rounded-full bg-white text-neutral-900 px-5 py-2.5 text-sm font-medium border border-neutral-300 hover:bg-neutral-50` | "How to Contribute", "Read the Blog" |
| Dark | `rounded-full bg-neutral-900 text-white px-5 py-2.5 text-sm font-medium hover:bg-neutral-700` | "View on GitHub" |

All buttons: `focus:outline-none focus-visible:ring-2 focus-visible:ring-[color]/60 transition duration-200`

### Cards

```
bg-white/90 rounded-xl p-4 space-y-3
transition-transform duration-300 hover:scale-[1.015] hover:shadow-md hover:shadow-neutral-200/50
```

### Section Containers

```
max-w-5xl mx-auto px-4 py-14
```

Callout sections use `bg-primary-50 rounded-2xl px-6 py-10`.

### NavBar

- Transparent when at top, `bg-white/80 backdrop-blur-md border-b border-neutral-200` on scroll
- Active link: `text-primary-700` + animated underline via `scale-x-100`
- CTA: `accent-cta` pill, right-aligned
- Mobile: slide-in drawer from right, `w-64`, backdrop blur

### Footer

- Three-column link grid (`FooterColumns`)
- Social icons: `w-11 h-11 rounded-full bg-white border border-neutral-200 shadow-sm hover:text-primary-700 hover:bg-primary-50 hover:scale-110`
- Copyright: `text-xs text-neutral-500`
- Scroll-to-top FAB: `fixed bottom-6 right-6 w-11 h-11 rounded-full bg-white border border-neutral-300 text-primary-700`

---

## Hero Pattern

The homepage hero uses a **violet gradient** background (no external photo):

```css
bg-gradient-to-br from-primary-900 via-primary-800 to-primary-700
```

With a radial highlight overlay for depth:

```css
bg-[radial-gradient(ellipse_at_top_right,_rgba(139,92,246,0.3)_0%,_transparent_60%)]
```

Layout: two-column on `lg+` (copy left, app screenshot right). Single-column stacked on mobile.

App screenshot displayed in: `rounded-xl overflow-hidden shadow-2xl shadow-primary-950/50 ring-1 ring-white/10`

---

## App Design Tokens (Desktop)

The Tauri app (`src/`) shares the same Tailwind color scale. Additional app-only tokens:

| Purpose | Value |
|---------|-------|
| Mood: excellent | `#10b981` (matches `mood-great`) |
| Mood: good | `#84cc16` |
| Mood: neutral | `#eab308` |
| Mood: low | `#f97316` (matches `mood-low`) |
| Mood: bad | `#ef4444` |
| Micro-interaction | `duration-200` |
| Page transition | `duration-300` |
| Min window size | `800 × 600` |

---

## Do / Don't

| Do | Don't |
|----|-------|
| Use `primary-700` for interactive text links | Use blue for links — this is a violet brand |
| Use `accent-cta` (#F28C38 orange) for primary CTAs only | Use orange for decorative or secondary elements |
| Use `AnimatedReveal` for all scroll-triggered entrance animations | Add custom `@keyframes` for entrance — use the shared wrapper |
| Use mood colors only in data visualization contexts | Use mood colors for UI chrome or status indicators |
| Proof-based feature copy — name the file, the algorithm, the concrete behavior | Abstract claims ("secure", "private", "fast") without evidence |
| `rounded-full` for buttons and badges | `rounded-lg` for interactive elements (reserved for cards) |
