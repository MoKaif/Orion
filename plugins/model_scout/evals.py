"""Small, transparent evaluation set shaped around Orion's real routing decisions."""
from __future__ import annotations

CASES = [
    ("mode", "What is on my calendar tomorrow?", "reflex"),
    ("mode", "Remember that I prefer morning flights.", "reflex"),
    ("mode", "Compare Qwen and Phi for our local extraction workload.", "reasoning"),
    ("mode", "Explain the tradeoffs in this database design.", "reasoning"),
    ("mode", "Research the best architecture and build a complete migration plan.", "deep_work"),
    ("mode", "Refactor the provider layer across the project.", "deep_work"),
    ("specialist", "What meetings do I have this week?", "calendar"),
    ("specialist", "Add a dentist appointment tomorrow at 10.", "calendar"),
    ("specialist", "Review this Python service for a race condition.", "software"),
    ("specialist", "Research the evidence for local-first knowledge systems.", "research"),
    ("specialist", "Why was my spending unusually high this month?", "finance"),
    ("specialist", "How did my sleep compare with my baseline?", "health"),
    ("specialist", "Help me think through this idea.", "generalist"),
    ("commitment", "Dentist appointment next Friday at 10 am.", "event"),
    ("commitment", "I might travel someday if work calms down.", "not_event"),
    ("commitment", "The meeting scheduled for tomorrow was cancelled.", "not_event"),
    ("commitment", "Submit the visa application by 2027-01-15.", "event"),
    ("commitment", "Today was tiring, but dinner was good.", "not_event"),
]

QUESTIONS = {
    "mode": {"type": "choice", "instructions": "How much cognitive work does this request need?",
             "criteria": {"reflex": "simple retrieval, capture, lookup, or action",
                          "reasoning": "comparison, review, explanation, or bounded planning",
                          "deep_work": "large research, creation, project, or refactor"}},
    "specialist": {"type": "choice", "instructions": "Which Orion specialist owns this request?",
                   "criteria": {"calendar": "time, meetings, appointments, commitments",
                                "software": "programming, repositories, debugging, code review",
                                "research": "investigation, sources, evidence synthesis",
                                "finance": "money, transactions, budgets, spending",
                                "health": "sleep, activity, workouts, personal health data",
                                "generalist": "none of the specialist domains"}},
    "commitment": {"type": "choice", "instructions": "Is this a concrete future commitment?",
                   "criteria": {"event": "explicit future appointment, deadline, meeting, or plan",
                                "not_event": "past, cancelled, routine, reflection, or vague wish"}},
}
