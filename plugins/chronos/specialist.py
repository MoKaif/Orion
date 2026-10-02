from orion.core import plugin_sdk as orion


class CalendarSpecialist(orion.Specialist):
    name = "calendar"
    description = "Explains upcoming commitments from Chronos and Google Calendar."
    keywords = ("calendar", "schedule", "upcoming", "appointment", "meeting", "deadline",
                "this week", "this month", "tomorrow", "chronos")
    tools = ("upcoming_schedule",)

    def system_fragment(self) -> str:
        return (
            "Use upcoming_schedule for actual calendar facts. Clearly separate confirmed Google "
            "Calendar events from mail-derived proposals awaiting approval. Never claim a proposed "
            "event is scheduled, and never invent dates or availability."
        )
