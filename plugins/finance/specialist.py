"""Finance chat specialist — evidence first, inference labelled."""
from orion.core import plugin_sdk as orion


class FinanceSpecialist(orion.Specialist):
    name = "finance"
    description = "Explains personal spending, cash flow, trends, anomalies, and financial patterns."
    keywords = (
        "spending", "spend", "expense", "expenses", "budget", "money", "finance",
        "income", "savings", "subscription", "transaction", "cash flow", "treasurer",
        "invested", "investment", "cost", "afford",
    )
    tools = ("finance_summary", "spending_breakdown", "finance_insights")

    def system_fragment(self) -> str:
        return (
            "You are acting as Treasurer's Finance specialist. Use the finance tools for the "
            "user's actual figures; never estimate totals from memory. Distinguish computed facts, "
            "model observations, tentative hypotheses, and suggestions. Explain the evidence and "
            "confidence behind a trend. Do not shame, diagnose, or present investment guidance as "
            "certain. FinStrive is read-only: never claim to have changed a transaction or budget."
        )
