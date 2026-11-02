"""DESIGN.md is the design source of truth: its colours must match style.css, and the text colours must keep their
contrast promises (4.5:1 on every surface they sit on) in both themes."""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "src" / "web" / "static" / "style.css").read_text(encoding="utf-8")
DESIGN = (ROOT / "DESIGN.md").read_text(encoding="utf-8")

# style.css token -> DESIGN.md colour name (without the theme prefix)
TOKENS = {"app": "app", "s1": "surface", "s2": "surface-raised", "s3": "surface-control", "s4": "surface-strong",
          "line": "line", "line2": "line-strong", "fg": "text", "fg2": "text-secondary", "fg3": "text-tertiary",
          "fg4": "text-muted", "accent-text": "accent-text"}
SURFACES = ("app", "s1", "s2", "s3")
TEXT_ROLES = ("fg", "fg2", "fg3", "fg4", "accent-text")


def front_matter():
    return yaml.safe_load(DESIGN.split("---")[1])


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def css_theme(selector):
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\}", CSS, re.S).group(1)
    return {m.group(1): tuple(int(x) for x in m.group(2).split()) for m in re.finditer(r"--([\w-]+):\s*(\d+ \d+ \d+);", block)}


def luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = map(channel, rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestDesignTokens(unittest.TestCase):
    themes = {"dark": css_theme(':root[data-theme="dark"]'), "light": css_theme(':root[data-theme="light"]')}

    def test_design_md_colours_match_the_stylesheet(self):
        colors = front_matter()["colors"]
        for theme, tokens in self.themes.items():
            for token, name in TOKENS.items():
                self.assertEqual(hex_rgb(colors[f"{theme}-{name}"]), tokens[token], f"{theme} {token}: DESIGN.md and style.css disagree")

    def test_text_keeps_4_5_to_1_on_every_surface_in_both_themes(self):
        for theme, tokens in self.themes.items():
            for role in TEXT_ROLES:
                for surface in SURFACES:
                    ratio = contrast(tokens[role], tokens[surface])
                    self.assertGreaterEqual(ratio, 4.5, f"{theme}: {role} on {surface} is {ratio:.2f}:1")

    def test_the_primary_button_label_is_readable(self):
        colors = front_matter()["colors"]
        self.assertGreaterEqual(contrast(hex_rgb(colors["on-accent"]), hex_rgb(colors["accent"])), 4.5)

    def test_neither_theme_uses_pure_black_or_white_for_text(self):
        for theme, tokens in self.themes.items():
            self.assertNotIn(tokens["fg"], ((255, 255, 255), (0, 0, 0)), f"{theme} primary text is pure black or white")

    def test_dark_surfaces_rise_in_lightness(self):
        dark = self.themes["dark"]
        steps = [luminance(dark[k]) for k in ("app", "s1", "s2", "s3", "s4")]
        self.assertEqual(steps, sorted(steps), "each dark surface step must be lighter than the one below")

    def test_every_component_reference_points_at_a_real_token(self):
        data = front_matter()
        for name, comp in data["components"].items():
            for key, value in comp.items():
                for ref in re.findall(r"\{([\w.-]+)\}", str(value)):
                    node = data
                    for part in ref.split("."):
                        self.assertIn(part, node, f"{name}.{key} points at missing token {ref}")
                        node = node[part]

    def test_the_document_has_the_standard_sections_in_order(self):
        headings = re.findall(r"^## (.+)$", DESIGN, re.M)
        self.assertEqual(headings, ["Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes", "Components", "Do's and Don'ts"])


if __name__ == "__main__":
    unittest.main()
