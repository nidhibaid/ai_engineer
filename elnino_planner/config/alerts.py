"""Which NWS alert events count as in scope for the MVP (contract 2.1, `relevant_to_mvp`).

Matched as lowercase substrings of the NWS `event` name, e.g. "Coastal Flood Warning" -> "flood".
Heat, air quality, fire and similar events are still shown, but marked not relevant to the MVP.
"""
MVP_ALERT_KEYWORDS: tuple[str, ...] = (
    "flood",        # Flood, Flash Flood, Coastal Flood, Lakeshore Flood
    "surf",         # High Surf
    "wind",         # Wind Advisory, High Wind
    "storm",        # Winter Storm, Severe Thunderstorm, Storm Warning
    "hydrologic",   # Hydrologic Outlook
    "rip current",
    "beach hazards",
)
