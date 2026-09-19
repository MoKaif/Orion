"""Health chat specialist — measurements first, interpretation bounded."""
from orion.core import plugin_sdk as orion


class HealthSpecialist(orion.Specialist):
    name = "health"
    description = "Explains personal activity, sleep, heart-rate, and workout patterns from Perseus."
    keywords = ("health", "steps", "sleep", "heart rate", "bpm", "activity", "workout",
                "distance", "active energy", "vitalist", "fitness", "recovery")
    tools = ("health_summary",)

    def system_fragment(self) -> str:
        return (
            "You are acting as Vitalist's health specialist. Use the health_summary tool for "
            "the user's actual Perseus measurements. Separate measurements from interpretations, "
            "state when data is missing, and do not diagnose, prescribe, or imply that tracker "
            "data replaces clinical advice. Never invent a measurement or health baseline."
        )
