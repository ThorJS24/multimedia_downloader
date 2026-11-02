---
version: alpha
name: Spotify Downloader Studio
description: A calm, exact desktop tool for building and keeping a music library. Graphite surfaces, one green accent, tabular figures.
colors:
  dark-app: "#0a0a0a"
  dark-surface: "#141414"
  dark-surface-raised: "#1c1c1c"
  dark-surface-control: "#242424"
  dark-surface-strong: "#303030"
  dark-line: "#262626"
  dark-line-strong: "#383838"
  dark-text: "#f2f2f2"
  dark-text-secondary: "#d1d5db"
  dark-text-tertiary: "#a8a8a8"
  dark-text-muted: "#8c8c8c"
  dark-accent-text: "#1db954"
  light-app: "#f4f5f7"
  light-surface: "#ffffff"
  light-surface-raised: "#f3f4f6"
  light-surface-control: "#e5e7eb"
  light-surface-strong: "#d1d5db"
  light-line: "#e5e7eb"
  light-line-strong: "#d1d5db"
  light-text: "#111827"
  light-text-secondary: "#374151"
  light-text-tertiary: "#4b5563"
  light-text-muted: "#5b6472"
  light-accent-text: "#0a6e30"
  accent: "#1db954"
  accent-hover: "#1ed760"
  on-accent: "#000000"
  warning: "#fbbf24"
  danger: "#f87171"
typography:
  body:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: 600
    lineHeight: 1.5
  caption:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.4
  title:
    fontFamily: Plus Jakarta Sans
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.3
  figure:
    fontFamily: JetBrains Mono
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.2
    fontFeature: '"tnum"'
  code:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: 8px
  md: 12px
  lg: 16px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: 12px
  button-secondary:
    backgroundColor: "{colors.dark-surface-control}"
    textColor: "{colors.dark-text}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: 12px
  card:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.lg}"
    padding: 20px
  input:
    backgroundColor: "{colors.dark-surface-raised}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.md}"
    padding: 12px
---

## Overview

Spotify Downloader Studio is a Windows desktop tool for people who want to keep a music library they own: add music, watch it download, fix what is wrong, and keep it tidy. Its character is **calm, exact and workmanlike**. People leave it open for hours, so the chrome stays quiet and the content (titles, counts, sizes, errors) carries the interest.

The world it belongs to is a record collector's workbench at night: graphite, a little paper white, one green that means "go". The signature is **tabular monospaced figures**: every count, size, duration and bitrate lines up in a column and reads as data.

The interface was cleaned of generated-UI habits with the [unslop](https://github.com/heydamir/unslop) method. Decoration is not a feature here.

## Colors

Two equally designed themes (dark by default, light, or follow Windows), each built from one neutral ramp. Surfaces step *lighter* as they rise in dark mode and are separated by hairlines, never by glows. Neither theme uses pure black or pure white for text.

- **Surfaces** (`app`, `surface`, `surface-raised`, `surface-control`, `surface-strong`): the page, cards, inputs and rows, buttons, and strong fills. Cards are lighter than the page in dark mode.
- **Text** in four roles: primary, secondary, tertiary, muted. Every role meets 4.5:1 on every surface it is used on, in both themes. That is enforced by `tests/test_design_tokens.py`.
- **Accent** `#1db954` marks the primary action, the selected item and the one thing that matters. It is used rarely. On dark it can be text; on light the darker `light-accent-text` is used as text so it reaches 4.5:1.
- **Semantic colours** (`warning`, `danger`) carry meaning only: a warning is amber, a failure is red. Nothing is coloured for decoration, and icons take their text role colour.

## Typography

Plus Jakarta Sans for the interface and JetBrains Mono for figures, paths and codes. Two working weights, 400 and 500, with 600 for emphasis; nothing heavier. Hierarchy comes from size and colour, not weight. Sentence case everywhere (no tracked capitals), no italics for emphasis, no negative letter-spacing on small text. The smallest text is 12px. Numbers use tabular figures so columns align.

## Layout

An app shell, not a page: a 240px sidebar (collapsible) and fluid content, a fixed player bar at the bottom. Five destinations (Home, Downloads, Library, Automation, Settings) with sub-tabs for the sections inside each. Spacing sits on a 4px grid: 4, 8, 16, 24, 32. Related things are closer together than unrelated things; cards in a row share one padding (20px). Tables shed lower-priority columns as the window narrows (title, artist, play and actions always stay).

## Elevation & Depth

Depth comes from surface steps and hairlines. Floating panels (menus, the command palette, dialogs) may carry one soft neutral shadow; nothing glows. Hover is a one-step surface change; the active state is a surface step with a hairline. Every interactive element has a visible keyboard focus ring.

## Shapes

One radius family: controls 12px, cards 16px, small chips 8px. Pills are for counts and status only, not for every control. A nested radius is the outer radius minus the padding between them.

## Components

- **Primary button**: accent fill, black label (8:1). One per view.
- **Secondary button**: control surface, primary text, hairline border.
- **Card**: raised surface, hairline border, 16px radius, 20px padding. No hover scale.
- **Input**: raised surface, strong hairline, accent border on focus.
- **Status**: quiet text or a small tinted chip; meaning comes from the words as well as the colour.
- **Toasts** persist for errors, count down for the rest, and can be copied.

## Do's and Don'ts

- Do keep one accent and use it for the primary action. Don't add a second brand colour or a gradient.
- Do let figures line up (tabular, mono). Don't mix number formats on one screen.
- Do explain failures in plain words with the next step. Don't show raw error text first.
- Do keep motion to feedback (a quick colour change, one short fade when a section opens). Don't loop, pulse, bounce or animate layout.
- Do honour Windows "reduce motion". Don't rely on motion to convey state.
- Do use the project's line icons at one size and weight. Don't use emoji or sparkle icons.
- Don't ship greeting headlines, exclamation marks, hype words or claims that cannot be checked.
