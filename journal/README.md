# Journal

This folder is a running log of work, decisions, and reflections for the `twstock-algo` project.

## Structure

```
journal/
├── README.md           ← this file
├── weekly/             ← one file per week, contains plan + daily reflections
└── decisions/          ← lightweight ADRs for architectural choices
```

## Weekly Files (`weekly/<YYYY-WNN>.md`)

One file per ISO week. Each file has three kinds of sections:

**Week plan** (written on Sunday or the first working day of the week):
- What the week is trying to accomplish
- Exit criteria or milestones targeted

**Daily sections** (`## <YYYY-MM-DD>`, appended each working day):
- **Did** — what was actually completed
- **Learned** — anything non-obvious discovered
- **Next** — what the next session should pick up from

**Saturday retrospective** (appended at end of week):
- What went well, what slowed things down, what to adjust

At the start of each session, read the current week's file to recover context.

## Decision Records (`decisions/<NNNN>-<slug>.md`)

Lightweight ADR format. Use when a non-trivial architectural or tooling choice is made.

```
# <NNNN> — <Title>

**Date:** <YYYY-MM-DD>
**Status:** Accepted

## Context
Why a decision was needed.

## Decision
What was decided.

## Consequences
What this locks in or rules out going forward.
```

Number sequentially from `0001`. Do not edit past decisions — if a decision is reversed, write a new record that supersedes the old one.
