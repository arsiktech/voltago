#!/usr/bin/env python3
"""SPA accessibility/contrast checks for Volta's React frontend.

Replaces scripts/qa_accessibility_static.py (retired with static/ in plans/009).
The legacy script asserted WCAG relative-luminance contrast ratios for the
vanilla static/styles.css design tokens; that surface is gone, but the
contrast contract in docs/design-system.md covers the SHIPPING UI,
so the check moves to frontend/src/index.css
-- the SPA's actual light/dark design tokens -- rather than being dropped.

Reuses the exact relative-luminance + WCAG contrast-ratio math and the same
thresholds (MIN_BODY_CONTRAST=4.5, MIN_UI_CONTRAST=3.0) the legacy script used.
It resolves only solid `#rrggbb` custom properties, same as the legacy parser
(no CSS engine, no third-party deps) -- that is equivalent rigor, not weaker.
Token pairs that are genuinely not statically resolvable this way (SPA uses
`color-mix(in oklab, ...)` for pill/badge tints and an alpha `rgba()` focus
ring, neither of which is a solid hex the stdlib can resolve without a real
CSS engine) are never silently skipped: they are listed in
`not_statically_checkable` in every run's output, so the gap stays visible
until a browser-based `getComputedStyle` check can cover them.

Requires no running server; reads frontend/src/index.css directly.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
INDEX_CSS = ROOT / "frontend" / "src" / "index.css"
MIN_BODY_CONTRAST = 4.5
MIN_UI_CONTRAST = 3.0  # Reserved for parity with the legacy thresholds; see
# NOT_STATICALLY_CHECKABLE below -- the SPA's only UI-boundary color (--ring)
# is an alpha rgba(), not solid hex, so no pair currently exercises this
# threshold. Kept defined so a future solid-hex UI pair can use it honestly.

# Design tokens required in both theme blocks (mirrors the legacy script's
# required-token guard, updated to the SPA's actual token names).
REQUIRED_TOKENS = ("bg", "surface", "fg", "muted", "go", "check", "verify", "brand", "brand-contrast")

# Primary reading pairs: (label, foreground token, background token, minimum).
# Same spirit as the legacy text/muted/mint/sun/warn-on-night pairs, expanded
# to the SPA's two real solid surfaces (page bg + card surface) and its three
# decision colors (go/check/verify), each checked in both themes below.
TEXT_PAIRS: List[Tuple[str, str, str, float]] = [
    ("body text", "fg", "bg", MIN_BODY_CONTRAST),
    ("body text on card surface", "fg", "surface", MIN_BODY_CONTRAST),
    ("muted helper text", "muted", "bg", MIN_BODY_CONTRAST),
    ("muted helper text on card surface", "muted", "surface", MIN_BODY_CONTRAST),
    ("go decision text", "go", "bg", MIN_BODY_CONTRAST),
    ("go decision text on card surface", "go", "surface", MIN_BODY_CONTRAST),
    ("check decision text", "check", "bg", MIN_BODY_CONTRAST),
    ("check decision text on card surface", "check", "surface", MIN_BODY_CONTRAST),
    ("verify decision text", "verify", "bg", MIN_BODY_CONTRAST),
    ("verify decision text on card surface", "verify", "surface", MIN_BODY_CONTRAST),
    # Pressed filter chip: `.chip[aria-pressed='true']` uses solid
    # brand/brand-contrast (unlike the pill/badge tints below, which use
    # color-mix and are not statically resolvable) -- same family as the
    # legacy script's hardcoded "dark text on mint/sun chip" pairs.
    ("pressed filter chip text", "brand-contrast", "brand", MIN_BODY_CONTRAST),
]

# Rendered pairs that are NOT statically resolvable with a solid-hex parser:
# the SPA uses color-mix(in oklab, ...) for pill/badge tint backgrounds and an
# alpha rgba() for the focus ring. A real check needs a browser
# getComputedStyle (or a full CSS color-mix/oklab resolver) -- tracked here
# explicitly rather than silently dropped.
NOT_STATICALLY_CHECKABLE = [
    {
        "pair": "go decision text on its own pill background",
        "css_rule": ".pill-go { color: var(--go); background: color-mix(in oklab, var(--go) 15%, transparent); }",
        "reason": "not statically checkable (color-mix); needs a browser getComputedStyle check",
    },
    {
        "pair": "check decision text on its own pill background",
        "css_rule": ".pill-check { color: var(--check); background: color-mix(in oklab, var(--check) 16%, transparent); }",
        "reason": "not statically checkable (color-mix); needs a browser getComputedStyle check",
    },
    {
        "pair": "verify decision text on its own pill background",
        "css_rule": ".pill-verify { color: var(--verify); background: color-mix(in oklab, var(--verify) 16%, transparent); }",
        "reason": "not statically checkable (color-mix); needs a browser getComputedStyle check",
    },
    {
        "pair": "focus-visible outline against arbitrary page content",
        "css_rule": ":focus-visible { outline: 2px solid var(--ring); } /* --ring is rgba(...) alpha, not solid hex */",
        "reason": "not statically checkable (alpha rgba over unknown backdrop); needs a browser getComputedStyle check",
    },
]

# Known, filed, tracked WCAG failures. Do NOT lower thresholds and do NOT fix
# the SPA design here to make this script green -- see qa/defects.csv for the
# defect id cited in each waiver. Format: {(fg, bg, theme): "D-###"}.
#
# D-149 (High, Prime Directive §2.6): porting this check from the retired
# static/ shell to the SPA's real frontend/src/index.css tokens (plans/009)
# revealed the light-theme go/check/verify decision colors and the
# pressed-filter-chip text were never contrast-tested and fell short of
# MIN_BODY_CONTRAST (2.88-4.04:1; the pressed chip measured 3.14:1). Fixed
# 2026-07-12 (Iteration 176) by darkening the four light-theme tokens in
# frontend/src/index.css (hue/saturation preserved, dark theme untouched --
# it already passed): go 2.88->4.68:1, check 3.07->4.61:1, verify
# 4.04->4.61:1, pressed-chip 3.14->4.66:1 (all measured against the stricter
# of --bg/--surface; every pair is now >=4.5:1). No pairing correction was
# needed -- reading the real components (StationCard.tsx's inline
# var(--go)/var(--check) text, DecisionPill's .pill-* classes,
# FilterRail.tsx's .chip[aria-pressed='true'] rule plus its layoutId
# spring-in bg-brand overlay) confirmed this check's fg/bg pairing already
# matched the real render exactly. WAIVED_PAIRS is now empty so the check
# ENFORCES these six pairs going forward instead of merely tracking them.
WAIVED_PAIRS: Dict[Tuple[str, str, str], str] = {}


def theme_block(css: str, selector_pattern: str) -> str:
    match = re.search(selector_pattern + r"\s*\{(?P<body>.*?)\}", css, re.S)
    return match.group("body") if match else ""


def css_vars(block: str) -> Dict[str, str]:
    return {name: value.strip() for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)}


def parse_hex(value: str) -> Tuple[float, float, float] | None:
    value = value.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return None
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))


def rel_channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color: Tuple[float, float, float]) -> float:
    r, g, b = color
    return 0.2126 * rel_channel(r) + 0.7152 * rel_channel(g) + 0.0722 * rel_channel(b)


def contrast_ratio(fg: str, bg: str) -> float | None:
    fg_rgb = parse_hex(fg)
    bg_rgb = parse_hex(bg)
    if fg_rgb is None or bg_rgb is None:
        return None
    lighter, darker = sorted([luminance(fg_rgb), luminance(bg_rgb)], reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def evaluate() -> Dict[str, object]:
    """Pure function (besides reading index.css): compute pass/fail without
    exiting, so both main() and tests/test_accessibility_spa.py can call it."""
    failures: List[str] = []
    waived: List[str] = []
    pair_results: List[Dict[str, object]] = []

    if not INDEX_CSS.exists():
        return {"ok": False, "failures": [f"missing {INDEX_CSS}"], "waived": [], "pairs": [], "not_statically_checkable": NOT_STATICALLY_CHECKABLE}

    css = INDEX_CSS.read_text(encoding="utf-8")
    themes = {
        "light": css_vars(theme_block(css, r":root")),
        "dark": css_vars(theme_block(css, r"\[data-theme=['\"]dark['\"]\]")),
    }

    for theme_name, vars in themes.items():
        for required in REQUIRED_TOKENS:
            if required not in vars:
                failures.append(f"{theme_name} theme missing CSS color token --{required}")
        if any(f"{theme_name} theme missing" in f for f in failures):
            continue
        for label, fg_token, bg_token, minimum in TEXT_PAIRS:
            fg_value = vars[fg_token]
            bg_value = vars[bg_token]
            ratio = contrast_ratio(fg_value, bg_value)
            key = (fg_token, bg_token, theme_name)
            row = {
                "theme": theme_name,
                "pair": label,
                "fg": f"--{fg_token}={fg_value}",
                "bg": f"--{bg_token}={bg_value}",
                "ratio": round(ratio, 2) if ratio is not None else None,
                "minimum": minimum,
            }
            if ratio is None:
                failures.append(f"[{theme_name}] contrast pair {label} is not a solid hex pair: {fg_value} on {bg_value}")
                row["ok"] = False
            elif ratio < minimum:
                defect_id = WAIVED_PAIRS.get(key)
                if defect_id:
                    waived.append(
                        f"[{theme_name}] {label} measured {ratio:.2f}:1 (< {minimum}:1) -- KNOWN DEFECT {defect_id}, not fixed here, not silently passed"
                    )
                    row["ok"] = False
                    row["waived_as"] = defect_id
                else:
                    failures.append(f"[{theme_name}] contrast pair {label} too low: {ratio:.2f}:1, expected >= {minimum}:1")
                    row["ok"] = False
            else:
                row["ok"] = True
            pair_results.append(row)

    if re.search(r"letter-spacing\s*:\s*-[^;]+;", css):
        failures.append("index.css contains negative letter-spacing")
    if ":focus-visible" not in css:
        failures.append("index.css missing focus-visible styles")
    if "prefers-reduced-motion: reduce" not in css:
        failures.append("index.css missing reduced-motion media query")

    return {
        "ok": not failures,
        "failures": failures,
        "waived": waived,
        "pairs": pair_results,
        "not_statically_checkable": NOT_STATICALLY_CHECKABLE,
    }


def main() -> int:
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
