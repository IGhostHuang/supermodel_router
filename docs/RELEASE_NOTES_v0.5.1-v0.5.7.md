---
title: SMR v0.5.1 to v0.5.7 Release Notes
date: 2026-08-09
version_range: 0.5.1 - 0.5.7
project: supermodel_router
---

# SMR v0.5.1 to v0.5.7 Release Notes

> 6 days of agent mode iteration culminating in reasoning_content fix.

## Headline

**v0.5.7 fixes the agent:fast Chinese-rendering bug** caused by Python `or` short-circuit
when `content` is empty string but `reasoning_content` has the actual answer
(reasoning models like deepseek-v4-flash return this way).

## Commits (chronological)

| SHA | Version | Theme |
|-----|---------|-------|
| (history) | v0.5.1 | agent:hybrid mode (MOA + ReAct + MOA) |
| (history) | v0.5.2 | SSE streaming for agent:* modes |
| (history) | v0.5.3 | agent:* registered in /v1/models |
| (history) | v0.5.4 | agent:fast (single LLM, 5-15s) |
| e91ae26 | v0.5.5 | hybrid speed-up (30-200s to 15-30s) + auto-dispatch |
| 2a43600 | v0.5.6 | fix bare `agent` routing (normalize to `agent:auto`) |
| 4b9e8cd | v0.5.6 | version metadata + project structure doc |
| a768b7b | v0.5.7 | merge reasoning_content into content |

## Breaking changes

None. All agent:* modes remain backward-compatible.

## New features

### agent mode matrix (v0.5.5+)

```
model="agent"          -> auto-dispatch
model="agent:fast"     -> single LLM, 5-15s, no tools
model="agent:moa"      -> multi-model vote, 12-60s, no tools
model="agent:auto"     -> ReAct + tools, 30-60s
model="agent:hybrid"   -> MOAx2 + ReAct, 15-30s, highest quality
```

### Auto-dispatch heuristic (v0.5.5)

```python
if has_tool_verb:        -> agent:hybrid
elif short_query:        -> agent:fast
elif wants_quality:      -> agent:moa
else:                    -> agent:fast
```

## Bug fixes

### v0.5.7: reasoning_content short-circuit

**Before**:
```python
raw_answer = c.get("content") or c.get("reasoning_content") or ""
```

**Bug**: `"" or X` returns `X` (the literal string "reasoning_content") because empty string is falsy.

**After**:
```python
_content = c.get("content")
_reasoning = c.get("reasoning_content")
if (not _content or not _content.strip()) and _reasoning and _reasoning.strip():
    c["content"] = _reasoning  # merge into content for Trae IDE
```

### v0.5.6: bare `agent` routing

**Before**: `agent` -> engine.pick_chain("agent") -> 429 to generic provider -> Unknown stream error.

**After**: `agent` -> normalize to `agent:auto` -> auto-dispatcher -> routes correctly.

## Integration: Trae IDE

| Field | Value |
|-------|-------|
| Custom URL | http://172.31.187.45:6473/v1 |
| Model | `agent` |
| Context | 262144 input / 16384 output |
| Tool rounds | 500 |

## Dev tooling: codegraph MCP

```bash
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh
codegraph init .
codegraph install --target hermes --location global --yes
```

SMR project indexed: 71 files / 2,150 nodes / 4,537 edges / 5.80 MB SQLite.

## Verification

```bash
# Health
curl -s http://localhost:6473/v1/health | jq '.version, .title'
# Should return v0.5.7

# Agent fast test
curl -s -X POST http://localhost:6473/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"agent:fast","messages":[{"role":"user","content":"ni hao"}],"stream":false}' \
  | jq '.choices[0].message.content'
```
