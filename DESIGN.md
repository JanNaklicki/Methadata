---
name: Methadata
description: "Polish-language workspace for comparing saved pages and ads."
colors:
  canvas: "#0a0e17"
  panel: "#0f131c"
  panel-raised: "#181b25"
  wash: "#1b2432"
  divider: "#283140"
  control-border: "#394659"
  ink: "#dfe2ef"
  muted: "#bbc9cf"
  primary: "#a5e7ff"
  primary-hover: "#cef2ff"
  accent: "#4fdbc8"
  graph-stage: "#0b111b"
  graph-grid: "#28364a"
  graph-selected: "#62daca"
  graph-quiet: "#536478"
  signal-image: "#8ab6ff"
  signal-text: "#eabb79"
  signal-html: "#bc9eec"
  accepted-surface: "#173238"
  accepted-text: "#79e2d6"
  insufficient-surface: "#29231c"
  insufficient-border: "#594930"
typography:
  headline:
    fontFamily: "Hanken Grotesk, Inter, system-ui, sans-serif"
    fontWeight: 650
    fontSize: "clamp(1.85rem, 3vw, 2.5rem)"
    lineHeight: 1.14
    letterSpacing: "-.035em"
  title:
    fontFamily: "Hanken Grotesk, Inter, system-ui, sans-serif"
    fontWeight: 650
    fontSize: "1rem"
    lineHeight: 1.14
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    lineHeight: 1.55
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: ".83rem"
    fontWeight: 650
  action:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: ".9rem"
    fontWeight: 690
  score:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "1.6rem"
    fontWeight: 650
  timestamp:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: ".76rem"
rounded:
  panel: "12px"
  comparison: "10px"
  segmented: "9px"
  control: "7px"
  image: "4px"
  dot: "50%"
spacing:
  control-gap: ".65rem"
  panel-padding: "1.1rem"
  comparison-gap: ".8rem"
  timeline-gap: "1.2rem"
  explorer-gap: "1.25rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.canvas}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: ".62rem .9rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-outline:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: ".62rem .9rem"
  field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: ".55rem .75rem"
  navigation-item-current:
    backgroundColor: "{colors.wash}"
    textColor: "{colors.primary}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: ".55rem .65rem"
  campaign-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.panel-padding}"
  status-accepted:
    backgroundColor: "{colors.accepted-surface}"
    textColor: "{colors.accepted-text}"
    rounded: "{rounded.image}"
    padding: ".15rem .35rem"
  evidence-score:
    backgroundColor: "{colors.panel-raised}"
    textColor: "{colors.accent}"
    typography: "{typography.score}"
    rounded: "{rounded.control}"
    padding: ".65rem .8rem"
---

# Design System for Methadata

## Overview

Methadata is a Polish-language investigation workspace for comparing saved pages and ads. Analysts need to see which pairs met the grouping criteria, trace the result to source material, and recognize when the available input cannot support a conclusion.

The collection uses a dark navy shell and a navigation rail. Place counts, relationships, captures, and provenance where analysts need them to follow a result from the collection to the source. The local Stitch export provides the [forensic design guide](stitch_campaign_intelligence_platform/forensic_intelligence/DESIGN.md), [explorer](stitch_campaign_intelligence_platform/campaign_explorer_methadata_simplified/screen.png), [graph](stitch_campaign_intelligence_platform/investigation_graph_methadata/screen.png), [evidence inspector](stitch_campaign_intelligence_platform/evidence_inspector_methadata/screen.png), and [timeline and simulator](stitch_campaign_intelligence_platform/campaign_timeline_engine_config_methadata/screen.png). The export includes HTML companions. The running interface uses saved collection data and the tokens in [the frontend stylesheet](frontend/src/styles.css).

## Colors

- **Primary cyan** marks primary actions, active navigation, focus outlines, and adjustable criteria.
- **Teal** highlights the selected pair’s score, simulation output, and accepted relationship labels. The graph has its own teal for selected nodes.
- **Signal colors** identify graphics and image hashes in blue, text and trackers in amber, structure and script hashes in violet, and domains in cyan. Within analysis status, the muted amber treatment identifies insufficient evidence.
- **Navy and slate** distinguish the shell, working panels, and inset regions. Use light text for labels and measured values, muted text for secondary context, and slate borders to separate adjacent regions.

## Typography

Hanken Grotesk serves headings and Inter serves body text and controls. JetBrains Mono distinguishes technical values. Latin and Latin Extended subsets load locally and cover Polish diacritics. The collection title scales from 1.85rem to 2.5rem. Panel headings are 1rem and filter labels are .83rem. Monospace distinguishes identifiers, collection metrics, scores, and timestamps. Numeric result rows use tabular figures where values need to align.

## Layout

The collection rail is 13.5rem wide and stays in place at desktop widths. The content area is capped at 98rem and uses fluid gutters. The explorer places cards beside recent material. The graph places the canvas beside its inspector, the evidence view pairs two equal capture columns, and the timeline places event history beside the simulator.

At 1050px and below, the rail becomes a horizontal navigation row that wraps. At 800px and below, the explorer, graph inspector, and timeline stack, and the metrics become a two-column grid. At 660px and below, stack the capture comparison in reading order. At 520px and below, arrange the rail in two equal columns, hide the secondary top-bar context, tighten the collection gutters, and limit the graph canvas to 21rem. The analysis flow similarly keeps the outcome, evidence, and contextual lists in reading order at narrow widths.

## Elevation & Depth

One-pixel dividers and navy tonal layers separate regions. Raised panels contain scores and simulator controls. The graph sits on a recessed 20px dot grid and stays within its own interactive canvas. Where users permit motion, the evidence panel fades in over .22s and travels 5px into place. Buttons and top navigation use .16s background and border transitions.

## Shapes

Use 12px corners for containers, 10px for individual comparison panes, and 7px for buttons and fields. Capture thumbnails fit inside their wells so the relevant source stays visible. Circular markers identify timeline events and graph material nodes. Square graph nodes distinguish observed artifacts.

## Components

- **Controls.** Primary buttons use a cyan fill and dark text. Secondary actions use a bordered navy surface. Text fields and selects have labeled targets, a 44px minimum height, and the shared keyboard focus outline.
- **Navigation.** The current collection view uses a slate fill, a blue border, and cyan text. The rail retains text labels beside its icons.
- **New analysis and results.** Give file comparison the primary action, show URL limitations beside the field, and keep the synthetic example in its own panel. Begin results with the outcome and counts. Prioritize accepted pairs and comparisons involving the submitted material. Give groups and material details adjacent space at wide widths.
- **Collection.** Show filtered counts and groups alongside recent material. Group cards pair collection metrics with previews of the closest recorded pair when available. A selected recent material shows its saved preview and the strongest recorded relationship in scope. The unassigned table shows recorded domains and assignment state. The Materials view retains import records and uploaded files.
- **Graph and inspector.** Build the relationship view from accepted links and the artifact view from observed shared domains, trackers, image hashes, and script hashes. Show the selected pair and score above the shared signal meters. Preserve a clear route to the full inspector. The expandable graph list provides a second way to reach nodes and relationships.
- **Pair evidence.** Identify the pair first, show its saved score and two source captures, then show source excerpts, family scores, thresholds, supplemental methods, and the artifact audit. Use the stored SHA-256 values and show an explicit missing state for older responses. Closing evidence returns to the previous view and restores focus to the opener when available, with the selected view control as a fallback.
- **Timeline and simulator.** Show recorded capture and import timestamps as separate event types. Let analysts adjust family weights and the threshold for a chosen pair and compare the local simulation with the saved score. Keep saved and simulated scores side by side. Use actual coverage, independent support, and the simulated decision.

## Do's and Don'ts

- Do keep outcome and relationship state labels visible alongside their color cues.
- Do keep focus visible and restore it when an evidence panel closes.
- Do display missing source captures, dates, or scores using the available empty states.
