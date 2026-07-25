# Orion Curator

Orion Curator is a local-first maintenance agent for an Obsidian vault. It scans Markdown notes, records metadata in SQLite, builds explainable change plans, and applies conservative changes that preserve the user's knowledge.

Default vault:

```text
D:\Kaif\Softlink\Softlink
```

## Quick Start

Run one safe preview:

```powershell
cd D:\Kaif\Curator
python .\orion_curator.py run --dry-run --limit 10
```

Run one live cycle:

```powershell
python .\orion_curator.py loop --once --limit 10
```

Run continuously:

```powershell
python .\orion_curator.py loop
```

Stop the loop with `Ctrl+C`.

## Core Workflow

Each cycle follows:

```text
scan -> plan -> validate -> apply -> audit -> sleep
```

The loop prints colored status lines for:

- `SCAN`: vault indexing and file counts.
- `PLAN`: deterministic and Ollama-backed plan generation.
- `QWEN`: local LLM review status.
- `APPLY`: note changes written to disk.
- `DONE`: cycle timing, approximate Python CPU, and RAM usage.
- `SLEEP`: time until the next cycle.

## Current Features

### Vault Indexing

Orion scans Markdown files in the configured vault and stores note metadata in SQLite:

- relative note path
- SHA-256 content hash
- active/deleted status
- last scanned time
- last modified time
- extracted wiki links
- extracted tags

Ignored folders are controlled by `ignoredFolders` in `config.json`.

### Markdown Cleanup

Orion can make deterministic low-risk formatting fixes:

- normalize line endings
- remove trailing whitespace outside code fences
- ensure one final newline

It does not edit fenced code blocks.

### Tags

When `autoTag` is enabled, Orion infers simple evidence-based tags from note title and body text. Existing tags are preserved, and new tags are merged into YAML frontmatter.

### Existing-Link Backlinks

When `autoBacklink` is enabled, Orion creates backlinks only from existing evidence.

Example:

```text
Note A contains [[Note B]]
```

Orion can add this to `Note B`:

```markdown
## Backlinks
- [[Note A]]
```

This helps Obsidian graph view show bidirectional relationships without inventing new links.

### Abstract Hub Notes

When `autoHubLinks` is enabled, Orion creates and maintains abstract graph hub notes under:

```text
Zettelkasten/Abstract
```

Default hubs:

- `Daily Logs`
- `Work`
- `Bug`
- `Feature`
- `Design`

Matching notes receive:

```markdown
## Related Hubs
- [[Zettelkasten/Abstract/Work|Work]]
```

Hub notes receive backlinks to grouped notes. Classification is deterministic and driven by `abstractHubs` rules in `config.json`, using folder and keyword signals.

### Ollama / Qwen Grammar Worker

When `useOllama` and `autoFixGrammar` are enabled, Orion asks local Ollama for low-risk grammar and readability improvements.

Current defaults:

```json
{
  "ollamaUrl": "http://localhost:11434",
  "ollamaModel": "qwen3.5:0.8b",
  "ollamaTimeoutSeconds": 120,
  "maxLlmChars": 6000,
  "maxLlmNotesPerRun": 1,
  "minLlmConfidence": 0.85
}
```

The LLM worker must return valid JSON. Orion rejects responses that:

- change code fences
- have low confidence
- change content length too much
- report medium/high risk
- fail JSON parsing

### LLM Skip List

If Qwen takes too long or a note is too large, Orion records that note in the LLM skip list. The skip is tied to the note's content hash, so editing the note allows Orion to try again later.

View skipped notes:

```powershell
python .\orion_curator.py llm-skips
```

Clear all skipped notes:

```powershell
python .\orion_curator.py clear-llm-skips
```

Clear skips matching one note:

```powershell
python .\orion_curator.py clear-llm-skips --note "ABI - Charge Mapper"
```

## Commands

### `scan`

Index the vault into SQLite.

```powershell
python .\orion_curator.py scan
```

### `plan`

Print proposed changes as JSON without applying them.

```powershell
python .\orion_curator.py plan --limit 10
```

### `run`

Scan, plan, and optionally execute changes depending on `applyChanges`.

```powershell
python .\orion_curator.py run --limit 10
```

Force preview mode:

```powershell
python .\orion_curator.py run --dry-run --limit 10
```

### `loop`

Run continuously on `scanIntervalMinutes`.

```powershell
python .\orion_curator.py loop
```

Run one cycle and exit:

```powershell
python .\orion_curator.py loop --once --limit 10
```

Print machine-readable JSON instead of colored logs:

```powershell
python .\orion_curator.py loop --json
```

### `llm-skips`

List notes that the LLM worker will skip for the current content hash.

```powershell
python .\orion_curator.py llm-skips
```

### `clear-llm-skips`

Clear persistent LLM skip records.

```powershell
python .\orion_curator.py clear-llm-skips
```

## Configuration

Main config file:

```text
D:\Kaif\Curator\config.json
```

Important settings:

```json
{
  "vaultPath": "D:\\Kaif\\Softlink\\Softlink",
  "applyChanges": true,
  "useOllama": true,
  "autoFixGrammar": true,
  "autoTag": true,
  "autoLink": true,
  "autoBacklink": true,
  "autoHubLinks": true,
  "maxChangesPerRun": 25,
  "maxLlmNotesPerRun": 1,
  "scanIntervalMinutes": 5,
  "backupBeforeChanges": true
}
```

Set `"applyChanges": false` to make Orion plan only.

## Safety Model

Orion prefers no change over a risky change.

- It never deletes notes.
- It never moves notes.
- It does not edit code fences.
- It records executed actions in SQLite.
- It creates backups before modifying existing notes when enabled.
- It uses deterministic evidence for tags, backlinks, and hub links.
- It validates LLM output before applying it.

Backups are stored in:

```text
D:\Kaif\Curator\.orion_backups
```

SQLite database:

```text
D:\Kaif\Curator\orion_curator.sqlite3
```

## Practical Operating Notes

Start with:

```powershell
python .\orion_curator.py loop --once --limit 10
```

If the output looks good, run:

```powershell
python .\orion_curator.py loop
```

If Qwen is too slow, lower one or more of:

```json
{
  "maxLlmNotesPerRun": 1,
  "maxLlmChars": 4000,
  "ollamaTimeoutSeconds": 60
}
```

If graph clustering is too broad or too narrow, tune `abstractHubs` in `config.json`.
