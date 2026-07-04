"""Inline SVG icon glyphs for floor plan markers.

Each glyph is a set of stroke ``<path>`` definitions on a 24x24 grid, drawn (not referenced by
``<use href>``) so the rendered floor plan stays self-contained for the "Save SVG" download, the same
rationale as embedding the blueprint as base64. Glyphs are simple line art in the spirit of Lucide.
"""

ICON_VIEWBOX = 24

ICON_GLYPHS = {
    "server": [
        "M3 4 h18 v6 h-18 z",
        "M3 14 h18 v6 h-18 z",
        "M6.5 7 h0.01",
        "M6.5 17 h0.01",
    ],
    "cpu": [
        "M6 6 h12 v12 h-12 z",
        "M9 9 h6 v6 h-6 z",
        "M9 3 v3",
        "M15 3 v3",
        "M9 18 v3",
        "M15 18 v3",
        "M3 9 h3",
        "M3 15 h3",
        "M18 9 h3",
        "M18 15 h3",
    ],
    "power": [
        "M4 3 h16 v18 h-16 z",
        "M13 6 L9 13 h3 l-1 5 4 -7 h-3 z",
    ],
    "plug": [
        "M9 2 v5",
        "M15 2 v5",
        "M7 7 h10 v3 a5 5 0 0 1 -10 0 z",
        "M12 15 v6",
    ],
    "monitor": [
        "M3 4 h18 v11 h-18 z",
        "M9 19 h6",
        "M12 15 v4",
    ],
    "camera": [
        "M4 8 h3 l1.5 -2 h7 l1.5 2 h3 v11 h-16 z",
        "M12 18 a4 4 0 1 0 0 -8 a4 4 0 1 0 0 8",
    ],
    "wifi": [
        "M4 11 a12 12 0 0 1 16 0",
        "M7.5 14.5 a7 7 0 0 1 9 0",
        "M10.5 18 a3 3 0 0 1 3 0",
        "M12 20.5 h0.01",
    ],
    "thermometer": [
        "M14 5 a2 2 0 0 0 -4 0 v9 a4 4 0 1 0 4 0 z",
    ],
    "phone": [
        "M7 2 h10 v20 h-10 z",
        "M10 18.5 h4",
    ],
    "printer": [
        "M6 9 V3 h12 v6",
        "M4 9 h16 v8 h-4 v4 h-8 v-4 h-4 z",
        "M8 13 h8",
    ],
    "building": [
        "M4 21 h16",
        "M6 21 V4 h8 v17",
        "M9 8 h0.01",
        "M12 8 h0.01",
        "M9 12 h0.01",
        "M12 12 h0.01",
        "M9.5 21 v-4 h3 v4",
    ],
    "layers": [
        "M12 3 L21 8 L12 13 L3 8 Z",
        "M3 12 L12 17 L21 12",
        "M3 16 L12 21 L21 16",
    ],
    # Hospital device vocabulary.
    "medical-equipment": [
        "M4 5 h16 v12 h-16 z",
        "M12 8.5 v5",
        "M9.5 11 h5",
    ],
    "patient-sensor": [
        "M2 12 h4 l2 -6 3 12 2 -8 2 4 h6",
    ],
    "nurse-call": [
        "M12 3 h0.01",
        "M7 18 v-5 a5 5 0 0 1 10 0 v5",
        "M5 18 h14",
        "M10 21 a2 2 0 0 0 4 0",
    ],
    "paging": [
        "M4 10 v4 h3 l6 4 v-12 l-6 4 z",
        "M17 9 a4 4 0 0 1 0 6",
        "M19.5 7 a7 7 0 0 1 0 10",
    ],
    "hvac": [
        "M4 5 h16 v14 h-16 z",
        "M7 8 h10",
        "M7 11 h10",
        "M7 14 h10",
        "M7 17 h10",
    ],
    "fan": [
        "M12 12 a2 2 0 1 0 0.01 0",
        "M12 10 C12 5 15 4 17 6 C18 8 16 11 12 12",
        "M14 13 C18 14 19 17 17 18 C15 19 12 16 12 12",
        "M11 14 C10 18 7 19 6 17 C5 15 8 12 12 12",
    ],
    "bed": [
        "M3 7 v11",
        "M3 13 h14 a4 4 0 0 1 4 4 v1",
        "M3 18 h18",
        "M6 13 v-2 h4 v2",
    ],
    "syringe": [
        "M13 4 l7 7",
        "M18 6 l-11 11 -3 3",
        "M6 15 l3 3",
        "M11 10 l3 3",
    ],
    "help": [
        "M9.4 9 a2.6 2.6 0 1 1 3.7 2.3 c-1 0.5 -1.6 1.1 -1.6 2.4",
        "M12 17.5 h0.01",
    ],
}

FALLBACK_ICON = "help"


def glyph_paths(icon_key):
    """Return the stroke path list for an icon key, falling back to the help glyph."""
    return ICON_GLYPHS.get(icon_key or FALLBACK_ICON, ICON_GLYPHS[FALLBACK_ICON])


def resolve_glyph(icon_key=None, custom_paths=None, viewbox=None):
    """Return ``(paths, viewbox)`` for a marker glyph.

    Custom path data (a list of SVG path-"d" strings, e.g. from a DB row or another app's type
    library) wins and carries its own viewbox; otherwise fall back to the built-in glyph for
    ``icon_key`` on the standard 24x24 grid.
    """
    if custom_paths:
        return list(custom_paths), (viewbox or ICON_VIEWBOX)
    return glyph_paths(icon_key), ICON_VIEWBOX
