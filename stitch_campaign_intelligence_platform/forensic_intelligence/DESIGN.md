---
name: Forensic Intelligence
colors:
  surface: '#0f131c'
  surface-dim: '#0f131c'
  surface-bright: '#353943'
  surface-container-lowest: '#0a0e17'
  surface-container-low: '#181b25'
  surface-container: '#1c1f29'
  surface-container-high: '#262a34'
  surface-container-highest: '#31353f'
  on-surface: '#dfe2ef'
  on-surface-variant: '#bbc9cf'
  inverse-surface: '#dfe2ef'
  inverse-on-surface: '#2c303a'
  outline: '#859399'
  outline-variant: '#3c494e'
  surface-tint: '#47d6ff'
  primary: '#a5e7ff'
  on-primary: '#003543'
  primary-container: '#00d2ff'
  on-primary-container: '#00566a'
  inverse-primary: '#00677f'
  secondary: '#4fdbc8'
  on-secondary: '#003731'
  secondary-container: '#04b4a2'
  on-secondary-container: '#003f38'
  tertiary: '#cddef8'
  on-tertiary: '#213145'
  tertiary-container: '#b2c2dc'
  on-tertiary-container: '#405065'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#b6ebff'
  primary-fixed-dim: '#47d6ff'
  on-primary-fixed: '#001f28'
  on-primary-fixed-variant: '#004e60'
  secondary-fixed: '#71f8e4'
  secondary-fixed-dim: '#4fdbc8'
  on-secondary-fixed: '#00201c'
  on-secondary-fixed-variant: '#005048'
  tertiary-fixed: '#d3e4fe'
  tertiary-fixed-dim: '#b7c8e1'
  on-tertiary-fixed: '#0b1c30'
  on-tertiary-fixed-variant: '#38485d'
  background: '#0f131c'
  on-background: '#dfe2ef'
  surface-variant: '#31353f'
typography:
  display-lg:
    fontFamily: Hanken Grotesk
    fontSize: 3rem
    fontWeight: '600'
    lineHeight: 3.5rem
    letterSpacing: -0.03em
  display-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 2rem
    fontWeight: '600'
    lineHeight: 2.5rem
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 2rem
    fontWeight: '500'
    lineHeight: 2.5rem
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 1.5rem
    fontWeight: '500'
    lineHeight: 2rem
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 1.5rem
    fontWeight: '500'
    lineHeight: 2rem
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Hanken Grotesk
    fontSize: 1.125rem
    fontWeight: '500'
    lineHeight: 1.625rem
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '400'
    lineHeight: 1.625rem
    letterSpacing: -0.005em
  body-md:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '400'
    lineHeight: 1.375rem
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
    letterSpacing: 0.005em
  mono-code:
    fontFamily: JetBrains Mono
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.25rem
    letterSpacing: -0.01em
  mono-metric:
    fontFamily: JetBrains Mono
    fontSize: 0.875rem
    fontWeight: '500'
    lineHeight: 1.25rem
    letterSpacing: 0em
  label-caps:
    fontFamily: Inter
    fontSize: 0.6875rem
    fontWeight: '600'
    lineHeight: 1rem
    letterSpacing: 0.06em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-mobile: 1rem
  margin: 3rem
  margin-tablet: 2rem
  margin-mobile: 1.25rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system delivers a calm, high-density, forensic intelligence workspace tailored for high-stakes incident responders, intelligence analysts, and national CERT authorities. The operational context requires prolonged scrutiny under cognitive load; the interface must actively minimize anxiety, eliminate synthetic drama, and strip away decorative artifice.

The visual style merges **Editorial Precision** with **Minimalist Utility**:
- **Calm by Default:** The UI repudiates the typical SOC dashboard syndrome characterized by neon alerts, chromatic badge soup, and gratuitous glow effects. Intelligence is presented with quiet confidence and spacious breathability.
- **Flat Surface Hierarchy:** Nested "cards within cards" are strictly eliminated. Elevation is established through muted tonal plane offsets, hairline division lines, and generous structural whitespace rather than heavy drop shadows.
- **Singular Intent:** Every screen must exhibit a clear primary focal point or action. Competing CTAs are suppressed into secondary text links or discreet contextual menus.
- **Forensic Restraint:** Monospace typography is reserved strictly for verifiable artifacts—SHA-256 signatures, autonomous system numbers (ASNs), IPv6 addresses, YARA rules, and deterministic timestamps.

## Colors

The palette is rooted in deep ink-obsidian tones, designed to sustain hours of intense analytical workflows without optical fatigue. Chromatic energy is severely rationed: color indicates actionable forensic anomalies, active navigation states, or singular interactive triggers, never decorative filling.

### Base & Surface Tokens
- **Canvas Base:** `#090d16` (Deep Obsidian Ink). The foundational stage upon which all data lives.
- **Surface Tier 1 (Containers/Nav):** `#0e1422` (Subtle midnight lift).
- **Surface Tier 2 (Panels/Inspector Drawers):** `#141c2e` (Muted panel fill for active forensic splits).
- **Surface Elevated (Overlays/Popovers):** `#1a243a` (High-contrast floating background).

### Foregrounds & Typography
- **Primary Text:** `#f8fafc` (Slate 50). Razor-sharp legibility for campaign titles, victim telemetry, and core findings.
- **Secondary / Supporting Text:** `#94a3b8` (Slate 400). Analytical commentary, field keys, and tabular metadata.
- **Subdued / Tertiary Text:** `#64748b` (Slate 500). Grid alignments, unit labels, inactive states, and structural hints.

### Accents & Signatures
- **Primary Insight (Cyan Accent):** `#00d2ff`. Applied exclusively to primary interactive triggers, validated campaign linkages, and verified intelligence flags.
- **Secondary Confirmation (Teal):** `#14b8a6`. Denotes benign or validated infrastructure checkpoints.
- **Structural Hairstyle (Border):** `rgba(255, 255, 255, 0.07)`. Razor-sharp 1px bounding line for separation without chromatic noise.
- **Forensic Threat Tiers (Muted Functional Alerts):** Critical `#f43f5e`, High `#fb923c`, Medium `#eab308`. Rendered with quiet ink fills (`rgba(color, 0.1)`) and solid 1px strokes; flashing indicators or high-luminance glows are prohibited.

## Typography

The typographic hierarchy enforces structural poise between editorial storytelling and technical telemetry. 

- **Primary Display & Headlines:** Set in **Hanken Grotesk**. Modern, unpretentious, sharp, and authoritative. Headlines must be formatted in sentence case to sustain an understated, journal-grade tone.
- **Narrative & System Interface:** Set in **Inter**. Optimized for sustained readability across dense tables, incident timelines, and analytical reports.
- **Deterministic Technical Telemetry:** Set in **JetBrains Mono**. Monospaced fonts must never be applied to human-authored narrative or generic status strings. They are reserved strictly for:
  - Cryptographic hashes (MD5, SHA-1, SHA-256).
  - Network indicators (IPv4/IPv6, CIDRs, MACs, ports, ASN strings).
  - Filepaths, registry strings, and code snippets.
  - Quantitative metrics requiring fixed-width horizontal alignment in tabular comparisons.
- **Letter Spacing:** Headlines employ tight negative tracking (`-0.01em` to `-0.03em`) for a sculpted, editorial finish. Small labels (`label-caps`) utilize uppercase with a `0.06em` tracking offset to anchor data columns cleanly without visual noise.

## Layout & Spacing

This design system uses a strict **fluid-column grid with structural whitespace offsets**. Density is achieved through intentional spatial compression within data clusters paired with generous breathing room around analytical sections.

### Grid & Spatial Architecture
- **Desktop (1200px+):** 12-column fluid grid. Max container width: 1536px. Outer page margin: `3rem` (48px). Column gutter: `1.5rem` (24px).
- **Tablet (768px – 1199px):** 8-column fluid grid. Outer page margin: `2rem` (32px). Column gutter: `1.5rem` (24px).
- **Mobile (<768px):** 4-column fluid grid. Outer page margin: `1.25rem` (20px). Column gutter: `1rem` (16px).

### The Anti-Nesting Rule
- Primary views must be composed of clean, flat layout lanes separated by whitespace (`space-xl`) or minimal divider lines.
- Deep component encapsulation ("cards containing cards containing tables") is forbidden. A data table or network graph occupies the canvas directly or sits inside a single primary container pane.
- Secondary telemetry shifts into an inspector drawer or contextual sidecar that shares the same elevation tier, keeping the depth stack flat and transparent.

## Elevation & Depth

Spatial elevation relies on **chromatic tonal steps and razor-sharp borders** rather than conventional, diffuse drop shadows.

- **Tonal Planes:** Depth is defined by transitioning from Canvas (`#090d16`) to Surface 1 (`#0e1422`) to Surface 2 (`#141c2e`). Each layer brightens minimally while remaining grounded in deep ink tones.
- **Razor Borders:** Surfaces use an ultra-fine, single-pixel hairline border (`1px solid rgba(255, 255, 255, 0.07)`). This provides structural separation while avoiding the heavy, industrial weight of brutalist borders.
- **Controlled Popover Elevation:** In scenarios where elements float over canvas data (e.g., context-action popovers, flyout menus, and command palettes), use a discrete edge backlight:
  - `box-shadow: 0 12px 32px -4px rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(255, 255, 255, 0.09)`
- **Atmospheric Clarity:** Blurred surfaces (frosted backdrops) are permitted exclusively behind fixed command navigation bars or sticky column headers (`backdrop-filter: blur(12px); background: rgba(9, 13, 22, 0.85)`). Soft glows, neon drop-shadows around buttons, and colored ambient halos are explicitly prohibited.

## Shapes

The shape system is tailored to feel exact, restrained, and contemporary:
- **Base Geometry:** Standard containers, data panels, input fields, and action buttons adopt a refined `0.5rem` (8px) corner radius (`rounded-base`).
- **Inner Nested Elements:** Badges, inner selection tokens, and segmented control segments inherit `0.25rem` (4px to 6px) to maintain concentric geometric balance with their parent frames.
- **Full Radius Restraint:** Pill shapes (`rounded-full`) are reserved exclusively for compact, single-digit numeric notification indicators or avatar nodes. They are never applied to primary buttons, search fields, or status chips.

## Components

### Buttons & Interactive Controls
- **Singular Primary Action:** High-contrast solid button with accent cyan background (`#00d2ff`), deep dark slate text (`#090d16`), weight `500`, radius `0.5rem`. Only one primary action is permitted per analytical view.
- **Secondary Action:** Ghost/Subtle tone. Surface-1 background (`#0e1422`), 1px border (`rgba(255, 255, 255, 0.07)`), text `#94a3b8`, transitioning on hover to text `#f8fafc` and border `rgba(255, 255, 255, 0.15)`.
- **Destructive Action:** Bordered slate with deep crimson text (`#fb7185`). No solid red fills unless confirming a final, non-reversible mitigation action.

### Status Indicators & Threat Badges
- Avoid heavy, saturated "chip soup."
- Status indicators must be rendered as minimalist inline pairings: an 8px monochromatic or muted-tint circular indicator paired with crisp, sentence-case metadata text (`#94a3b8`).
- Threat tier badges utilize an ink fill (`rgba(threat-color, 0.08)`) with matching `1px` subtle borders, suppressing visual alarm while retaining forensic clarity.

### Forensic Data Tables
- Header row: Uppercase tracking (`label-caps`), text `#64748b`, height `2.5rem`, border-bottom `1px solid rgba(255, 255, 255, 0.07)`.
- Data row: Height `2.75rem`, text `#f8fafc`, font `Inter` (or `JetBrains Mono` for artifacts), seamless border-bottom `1px solid rgba(255, 255, 255, 0.04)`.
- Row hover: Subtle shift to `background: rgba(255, 255, 255, 0.02)`. No abrupt high-contrast alternating zebra striping.

### Input Fields & Search Bars
- Background: Surface 1 (`#0e1422`) with `1px solid rgba(255, 255, 255, 0.07)`.
- Focus state: Subtle stroke shift to `1px solid rgba(0, 210, 255, 0.5)`. No thick multi-pixel outer rings.
- Text: Crisp white `#f8fafc` with placeholder text in subdued slate `#64748b`. Monospaced format enabled for query languages (e.g., Lucene, KQL, or regex queries).

### Incident & Evidence Cards
- Built as flat layout nodes directly on the canvas with a `1px` hairline boundary.
- Header: Incident identifier (monospace `#00d2ff`), title in `Hanken Grotesk` (`#f8fafc`), and a subdued timestamp (`#64748b`).
- Body: Analytical synopsis with strict avoidance of nested containers. Internal data points are arranged in horizontal grid lanes delimited by subtle vertical hairlines.