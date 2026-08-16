# Treasurer

Treasurer is Orion's read-only personal-finance agent. It reads FinStrive transactions, applies
the same account-flow semantics as FinStrive's dashboard, learns expected spending ranges, and
asks an LLM to interpret only findings whose figures were computed first.

## Trust boundary

- FinStrive remains the source of truth; Treasurer has no write tool or write endpoint.
- Raw transaction descriptions are processed locally and are not copied into the World Model.
- Merchant names are withheld from LLM prompts by default.
- ML is local. If scikit-learn is unavailable or history is thin, robust median/MAD baselines
  continue to work and the agent labels the method honestly.
- An LLM may explain a finding or offer a tentative hypothesis. It may not change its numbers.
- A financial inference becomes durable World Model knowledge only when the user chooses
  **Remember this pattern** in the Review Inbox.

## Setup

FinStrive must expose its existing transaction API at `http://127.0.0.1:5101` by default.
Override the URL in the gitignored `config/treasurer.local.json` layer. If FinStrive protects
the endpoint, place `FINSTRIVE_TREASURER_TOKEN` in `config/secrets.json`; never commit it.

The three jobs are visible and retunable on `/agents/treasurer`:

- `treasurer_refresh` refreshes the derived cache every four hours.
- `treasurer_analyze` runs models and LLM interpretation at 07:00, before Herald's briefing.
- `treasurer_train` validates the personal model weekly without spending an LLM turn.

Trigger **Find financial patterns** once after setup. Treasurer then exposes its snapshot and
evidence ledger under `/plugins/finance`, finance tools in chat, a dashboard card, Review Inbox
feedback, and sections/alerts in Herald.

## Inference pipeline

```text
FinStrive transactions
  → account-flow classification
  → daily/category features
  → robust baseline + optional quantile boosting / Isolation Forest
  → validated evidence record
  → constrained LLM explanation and hypothesis
  → Treasurer UI, review inbox, and Herald
```

Every insight retains its method, confidence, evidence JSON, model run, first/last observation,
resolution state, and user feedback in `data/treasurer.db`.
