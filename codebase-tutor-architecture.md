# Codebase Tutor — Architecture

Oct 3, 2026 · @Preshu

## Overview

The Codebase Tutor is a local-first command-line tool that teaches a developer her own codebase, and checks she understands each architectural decision before and after code is written. It is built for one real person: a software-engineer friend who asked why she should keep learning when an AI agent can write the code.

It runs on her laptop with an open-weight model served by Ollama. Her code never leaves her machine, the model is a setting she can swap, and it costs nothing to run.

It is an entry for the Hacktober "Build for a Friend" challenge, where open-source AI has to be what makes the project work.

## Architecture decisions

Seven decisions shape the system; each one and its reason is below.

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| 1 | Product shape | Local-first CLI that reads her repo straight from disk | Her code stays on her machine, and it is the simplest thing to build. Render only hosts a landing and demo page. |
| 2 | The gate | Soft gate: 2 to 3 options with explanations, an "I don't understand" path, and a skip at any time | The tool is there for when she wants to learn, and never nags her. |
| 3 | Code context | Repo map built once, plus read-only tools the model calls on demand | Answers are grounded in her actual files, it fits a small context window, and no vector database is needed. |
| 4 | Memory | Concept mastery, skipped questions, decision log, session transcripts | Stored as plain files she can open, inspect or delete. |
| 5 | Model layer | One swappable model behind Ollama's OpenAI-compatible endpoint, with an optional hosted open-weight fallback | The target machine has 8GB RAM and no GPU, so only roughly 1B to 4B parameter models run. Code does the heavy lifting and each model call has one narrow job. |
| 6 | Agent loop | Model-driven loop with guardrails; the driver is a separate, swappable module | More capable, and fits the open agent harness theme. A small model may wander, so the fallback is a larger model or a code-driven pipeline using the same tools. |
| 7 | MVP scope | All three commands (`plan`, `review`, `tour`), each kept thin | A fuller story for the challenge post. The cut order is in the MVP section. |

## System components

```text
+----------------------------- Her laptop -----------------------------+
|                                                                      |
|  CLI / Textual UI    plan | review | tour                            |
|  (options, "I don't understand", skip)                               |
|          |                                                           |
|          v                                                           |
|  Session driver ------------> Model adapter ------> Ollama           |
|  model-driven loop,           one OpenAI-compatible (Gemma, local)   |
|  step limit,                  interface; the model                   |
|  loop detection               name is a config setting               |
|          |                           :                               |
|          v                           :                               |
|  Read-only tools                     :                               |
|  read_file, grep, list_dir,          :                               |
|  git log, repo map, memory           :                               |
|          |                           :                               |
|          v                           :                               |
|  .tutor/ files: repo map, mastery,   :                               |
|  skipped, decisions, sessions        :                               |
|                                      :                               |
+--------------------------------------:-------------------------------+
                                       :
                                       v   (optional fallback)
                               Hosted open-weight endpoint
```

The CLI hands each request to the session driver, which calls the model through one adapter and uses the tools to read her repo, the repo map and memory. The hosted endpoint is optional and is the only part outside her laptop.

## Core flows

`plan` is the core of the product; `review` and `tour` reuse the same parts.

### plan "add a feature"

1. Load her memory and the repo map.
2. The agent loop finds the relevant files and reads them with the read-only tools.
3. The tutor explains the architectural implications in plain language, citing files and lines.
4. It asks 2 to 3 questions. Each has 2 to 3 recommended answers with a short explanation of each.
5. She picks an option, says "I don't understand", or skips.
6. On "I don't understand", it explains the concept using her own code and asks one check question.
7. It updates mastery, the skipped list and the decision log.

### review

1. Read the diff after the change was made.
2. Explain what changed, against the decisions recorded during `plan`.
3. Ask a couple of questions, with the same options, "I don't understand" and skip behavior.
4. Update memory.

### tour

1. Start at the repo's entry points and move outward through the repo map.
2. At each stop, explain what the module does and why it is built that way, labeling each reason documented, inferred or confirmed.
3. Ask one question per stop, with the same options and skip.
4. Update memory.

## Invariants and guardrails

These rules hold in every command, whatever model is running.

- **Read-only.** The tutor can read her code but never edits it; its job is teaching, not building.
- **Cited.** Every claim about her code points to a file, and the agent loop must return those references with its answer.
- **Honest about the "why".** Every reason given is labeled documented (from a commit message or doc), inferred (the model's guess) or confirmed (she agreed). It never presents a guess as fact.
- **Skip is free.** She can skip any question at any time without a penalty or a nag. Skipped topics are logged and offered again only at a calm moment, such as the end of a `review`.
- **Bounded loop.** A step limit per turn, a strict tool-call format with validation and one retry, and loop detection so the model doesn't keep re-reading the same file.
- **Narrow model calls.** Each call has one job, a short context, structured output where possible, and streamed responses so it feels responsive.
- **Local by default.** Nothing leaves her machine unless she points the model setting at a hosted endpoint herself.

## Memory layout

Everything the tutor remembers is stored as plain files in a `.tutor/` folder inside her project, so she can open, inspect or delete any of it. The file names below are a starting point for the build.

| File | Stores | Updated |
| --- | --- | --- |
| `.tutor/repo-map.json` | File tree, imports between files, symbols, and module summaries keyed by file hash | Built on first run; refreshed when files change; summaries written lazily |
| `.tutor/mastery.json` | Per concept: solid or shaky, and when it was last seen | After each question |
| `.tutor/skipped.json` | Questions she skipped, with topic and date | When she skips; cleared when she answers later |
| `.tutor/decisions.json` | Each decision, its reason, its label (documented, inferred or confirmed) and the source file | After `plan`, `review` and `tour` |
| `.tutor/sessions/*.jsonl` | Full transcript of each session, one append-only file per session | Live during the session |

## Tech stack

The stack is Python end to end, with Textual for the screen and Gemma served locally through Ollama.

| Layer | Choice | Notes |
| --- | --- | --- |
| Language and tooling | Python, `uv`, `pyproject.toml`, `ruff`, `pytest`, type hints throughout | Installed as a `tutor` command. Small modules that match the components in the diagram. |
| Terminal UI | Textual | A thin layer that only displays events from the session engine. The engine never imports the UI. |
| Code parsing | tree-sitter | Functions, classes and imports per file, and the import graph. Falls back to file tree and git history for languages without a grammar. |
| Agent loop | PydanticAI | Wrapped behind our own driver interface so the driver stays swappable. Loop detection is our own code. |
| Model | Gemma through Ollama | One config setting. Use the largest Gemma that fits 8GB when quantized, and check Ollama's library for a tools tag. Optional hosted open-weight fallback behind the same setting. |
| Storage | Plain JSON and JSONL in `.tutor/`, validated with Pydantic | A small `config.toml` holds the model name, endpoint and step limit. |
| Testing | `pytest` with a fake scripted model for the engine, plus a few Textual pilot tests | Engine tests run without Ollama. |
| Hosting | None | Runs locally on her laptop. Render is skipped for now. |

## MVP scope and cut order

All three commands ship at a basic level, and `plan` plus the memory behind it must work above all else. If time runs short, drop things in this order:

1. `tour` goes first.
2. Browsing past transcripts goes next; sessions are still saved to disk.
3. Polish on `review` goes after that.

Never cut: the `plan` flow, the options with "I don't understand" and skip, memory updates, and file citations with documented, inferred or confirmed labels.

## Build order

Build in this order, and finish each step's check before starting the next. Steps 0 to 6 produce a working `plan` in plain text, which is the point where the product exists; everything after that is layered on top.

| Step | Build | Done when |
| --- | --- | --- |
| 0 | Pre-flight: her laptop specs and project language, then Ollama with Gemma, and the three smoke tests on her real repo (summarize a module and cite the file, follow a three-step tool loop, return valid structured options) | The model passes, or the fallback (a hosted open-weight model, or a different local model for tool calls) is chosen |
| 1 | Project skeleton: `uv` project, package layout matching the components in the diagram, `config.toml`, `ruff`, `pytest` | `tutor --help` runs and the test suite runs |
| 2 | Memory store: Pydantic models and JSON or JSONL read and write for mastery, skipped questions, decisions and session transcripts | Round-trip tests pass for every file in `.tutor/` |
| 3 | Repo map: tree-sitter extraction, file tree, import graph, git history highlights, summaries cached by file hash and written lazily | The map builds on her real repo, and a file in an unsupported language falls back without crashing |
| 4 | Read-only tools: `read_file`, `grep`, `list_dir`, `git log`, repo map lookup, memory lookup | Unit tests pass, and every tool refuses a path outside her repo and can never write |
| 5 | Model adapter and driver: Ollama through PydanticAI behind our driver interface, step limit, validation with one retry, loop detection, and the fake scripted model for tests | A tool-using turn works with the fake model, and then with Gemma |
| 6 | Session engine and the `plan` flow with a throwaway text front end: explanation with file citations, options, "I don't understand", skip, and memory updates | A full `plan` runs on her real repo in plain text, with every reason labeled documented, inferred or confirmed |
| 7 | Textual UI on top of the engine's events: explanation panel, option select, streamed text | The `plan` flow runs inside Textual, and the engine still runs without it |
| 8 | `review`: read the diff, compare it with the recorded decisions, ask a couple of questions, update memory | A review runs after a real change made following a `plan` |
| 9 | `tour`: from the entry points outward, one question per stop | A tour runs across her repo; this is the first thing cut if time runs short |
| 10 | Hand-over: install on her machine, watch her use it, record her reaction, make the demo GIF, write the post | She has used it on her own project and you have her words |

## Open items and next steps

The first task is to test a small model on her real repo before building anything else.

- [x] Find out her laptop's specs and her main project's language, then run the three smoke tests on a Gemma model against her real repo (build step 0).
- [x] Decide the model fallback if a smoke test fails: a larger hosted open-weight model, or a different local model for the tool-calling role.
- [x] Decide whether Backboard fits at all, since it is a hosted service and the design is local-first.
- [ ] Hand it to her, record her reaction, and write the challenge post.
