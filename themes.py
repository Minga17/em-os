"""Color themes 🎨. A few unlock as you level up, as a little reward.

The colors themselves live in static/style.css ([data-theme="..."] blocks).
`bar` is the color of the phone's status bar for each theme.
"""
from i18n import N_

THEMES = {
    "midnight": {"name": N_("Midnight"), "emoji": "🌙", "level": 1, "bar": "#161b25",
                 "swatch": ("#0d1117", "#4a9eff")},
    "light":    {"name": N_("Light"), "emoji": "☀️", "level": 1, "bar": "#ffffff",
                 "swatch": ("#f4f6fb", "#2563eb")},
    "auto":     {"name": N_("Match my phone"), "emoji": "🌓", "level": 1, "bar": None,
                 "swatch": ("#0d1117", "#f4f6fb")},
    "ocean":    {"name": N_("Ocean"), "emoji": "🌊", "level": 2, "bar": "#0d262d",
                 "swatch": ("#071a1f", "#0e9fb0")},
    "forest":   {"name": N_("Forest"), "emoji": "🌲", "level": 3, "bar": "#121f17",
                 "swatch": ("#0b140f", "#22a35a")},
    "sunset":   {"name": N_("Sunset"), "emoji": "🌅", "level": 5, "bar": "#22141b",
                 "swatch": ("#170d12", "#f0587a")},
}
DEFAULT = "midnight"


def theme_of(user):
    theme = user["theme"] if user is not None and "theme" in user.keys() else DEFAULT
    return theme if theme in THEMES else DEFAULT


def unlocked(theme, level):
    return theme in THEMES and level >= THEMES[theme]["level"]
