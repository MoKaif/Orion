from orion.core import plugin_sdk as orion


class CalendarSpecialist(orion.Specialist):
    name = "calendar"
    description = "Explains upcoming commitments from Chronos and Google Calendar."
    keywords = ("calendar", "schedule", "upcoming", "appointment", "meeting", "deadline",
                "this week", "this month", "tomorrow", "chronos")
    tools = ("upcoming_schedule", "create_calendar_event")

    def system_fragment(self) -> str:
        return (
            "Use upcoming_schedule for actual calendar facts. Clearly separate confirmed Google "
            "Calendar events from mail/journal proposals awaiting approval. Use "
            "create_calendar_event when the user explicitly asks to add an event; its confirmation "
            "gate is mandatory. Never claim a proposed event is scheduled, and never invent dates, "
            "times, or availability."
        )
