"""Page formats. `cells` lays several records onto one sheet."""

PRESETS = {
    "a4": {"label": "A4 Hochformat", "w": 210, "h": 297},
    "a4l": {"label": "A4 Querformat", "w": 297, "h": 210},
    "a5": {"label": "A5 Hochformat", "w": 148, "h": 210},
    "a4-2up": {"label": "A4, 2 Blätter zum Schneiden", "w": 210, "h": 297, "cols": 1, "rows": 2, "cut": True},
    "a4-4up": {"label": "A4, 4 Blätter zum Schneiden", "w": 210, "h": 297, "cols": 2, "rows": 2, "cut": True},
    "label-3474": {"label": "Etiketten 70 × 37 mm (3 × 8)", "w": 210, "h": 297, "cols": 3, "rows": 8},
    "label-l7160": {"label": "Etiketten 63,5 × 38,1 mm (3 × 7)", "w": 210, "h": 297, "cols": 3, "rows": 7, "pad_x": 7, "pad_y": 15},
    "card": {"label": "Ausweiskarte 85,6 × 54 mm", "w": 85.6, "h": 54},
}
