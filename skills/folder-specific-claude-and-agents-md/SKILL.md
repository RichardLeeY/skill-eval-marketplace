---
name: folder-specific-claude-and-agents-md
description: Write a folder-scoped CLAUDE.md, plus an AGENTS.md symlink beside it, giving future agents the context a repo-wide CLAUDE.md does not cover. Use when the user asks for a CLAUDE.md or AGENTS.md for a particular directory, wants folder instructions or folder-scoped agent context, wants a subproject's conventions and locked decisions written down, or says agents keep repeating the same mistake in one part of the repo.
---

# Folder-scoped CLAUDE.md

Produce two things inside the target folder:

- `CLAUDE.md` — the folder's own agent context.
- `AGENTS.md` — a **symlink** to it, so agents that read `AGENTS.md` (Codex,
  Cursor, Gemini CLI) see the same file and edits never diverge.

Define `<folder>` as the target directory and `<skill-dir>` as this skill's root,
which the `Skill` tool result names. Every path below is relative to one of them.

## Step 1 — Identify the folder, and check it deserves a file

Take `<folder>` from the request. If the request names no folder, `LS` the
workspace and pick the one it describes; say which you picked and why in your
reply.

A folder earns a `CLAUDE.md` only when it carries context needed across multiple
sessions: evolving work, local conventions, decisions that must not be
re-litigated, traps that have already bitten someone. A folder of static
reference files does **not** — an agent can read those on demand, and a file that
restates them is pure token cost.

If the folder does not earn one, say so and stop. Do not write a file to have
written something.

## Step 2 — Read the folder in full

`LS <folder>` first, then `Read` every markdown, config, and significant source
file it lists. Not a skim, not a sample: every rule you write has to trace back
to something you actually read, and Step 5 forbids inventing the rest.

For a large subproject, "significant" means the dependency manifest, the entry
point, one representative module, and any file that reads like notes or
decisions. Say in your reply which files you read.

## Step 3 — Choose the sections

Use only the sections the folder's contents actually support. Skip the rest —
an empty section invites the next agent to fill it with boilerplate.

| Section | Holds |
|---|---|
| Purpose | What this folder is and its current state |
| Essential Files | One line per file that matters: its *role*, not its existence |
| Constraints (MUST NOT) | Hard negatives. The highest-value content in the file |
| Conventions | Naming patterns, lingo, status markers, "usually do X" |
| Locked Decisions | Agreed and dated. Must not be re-argued |
| Context | History and authority that frames the work |
| Working style | How to collaborate in this folder specifically |

**Constraints and Conventions are different sections on purpose.** A hard
"MUST NOT reintroduce the dedupe table" and a soft "modules are usually named
`normalize_<source>.py`" get followed at different rates when they are separated,
and both get diluted when they are mixed.

**A `MUST NOT` belongs here. A "must always happen" does not.** The two look alike
and behave differently. "MUST NOT reintroduce the dedupe table" works because an
agent that has read the line has everything it needs to comply. "Always run
`validate.py` before reporting" does not: this file is a prompt, so the step is
followed probabilistically and skipped silently, and nothing anywhere says it was
skipped. See [Steps that must happen](#steps-that-must-happen).

If a user is present and the folder is large or the right scope is unclear,
propose this section list and wait before writing. Otherwise write the file and
state in your reply which sections you chose and what you left out.

## Step 4 — Write `<folder>/CLAUDE.md`

- Open with one line saying what the file is for.
- **Subdirectory marker:** if any parent directory already has its own
  `CLAUDE.md`, the first content line must be exactly:
  `Apply root CLAUDE.md first, then this file.`
- `##` headers, in the order chosen in Step 3.
- Short bullets over prose.
- **Cross-file references use `@relative/path/file.md` import syntax**, not prose
  mentions, so the agent resolves them instead of guessing.
- Annotate any heavy reference doc with a `**Read when:**` trigger — e.g.
  `**Read when:** changing the Athena partition layout`. Without it, agents load
  it every session.
- **Every Constraint and Locked Decision names its source in the same bullet** —
  the file, and the comment, line or section it came from: `(cluster.tf, comment
  above eks_managed_node_groups)`, `(notes.md, 2024-06 incident)`, `(user, this
  request)`. A rule that reads "drain, never cordon" with no source is
  indistinguishable from generic best practice and gets re-argued or ignored; the
  same rule with `(cluster.tf, …)` after it tells the next agent where to look
  before changing it, and lets the audit in [Maintenance](#maintenance) check
  whether the source still says so.

## Step 5 — Verify

```bash
bash <skill-dir>/scripts/verify.sh <folder>
```

It creates `AGENTS.md` as a symlink to `CLAUDE.md` if it does not already exist,
then checks the pair and the file's shape, and prints one `PASS:` or `FAIL:`
line. Fix what it reports and run it again until it passes. Report the result.

## Rules

- **Never invent content.** Every bullet traces to a file you read or something
  the user said, and Constraints and Locked Decisions say which (Step 4). Generic
  best practice that would fit any folder is noise here.
- **No file trees, no directory listings, no stack description the code already
  shows.** Anything derivable from `LS` or `grep` rots on the next commit and
  spends tokens saying what the agent can see. Pin decisions, rules and context —
  never structure.
- **Folder-scoped only.** Never restate what a parent `CLAUDE.md` already says.
  Duplicated rules drift apart, and then the agent has two sources of truth.
- **A rule that holds in every folder does not belong in any folder's file.**
  Session hygiene, review process, spend limits, how to hand off context — all true
  here and everywhere, so they belong to a repo-wide or user-level `CLAUDE.md`, or
  to the settings and hooks that can act on them. Put one in a folder file and you
  have created the second source of truth the rule above warns about, in the copy
  nobody will remember to update.
- **No absolute ALWAYS or NEVER without its exceptions.** "Never commit secrets
  except `.env.example`" is followed; "never commit secrets" gets ignored the
  first time an exception is obvious.
- **Symlink, not a copy.** Two real files diverge; that is the whole failure this
  step prevents.
- **Brevity wins.** Start tight. It is easier to grow a file deliberately than to
  prune one that grew on its own.
- **Never bulk-summarize an existing file.** When asked to trim, cut specific
  lines by hand and say which. Auto-shortening collapses exactly the specific
  detail that made the file worth having.
- **Flag contradictions honestly.** If the folder's own files disagree — or the
  user's edits introduce a conflict — say so rather than silently picking one.
- **Emojis only where the folder already uses them** as status markers.
- **Do not commit.** Leave the files staged for the user unless they ask.

## Steps that must happen

Some of what a folder needs enforced is not documentation at all, and writing it
here is worse than leaving it out: the line makes the rule look handled while
nothing holds it. Sort by what the rule needs in order to hold.

| The rule needs… | Where it goes | Why not here |
|---|---|---|
| the agent to **know** something — a constraint, a dated decision, a trap that already bit | this file | nothing else carries it, and knowing is sufficient |
| a step to **run every time** | a hook, a CI step, or a script the workflow already calls | a prompt is followed probabilistically and skipped in silence |
| an action to be **impossible** | credentials, IAM, file permissions | a document cannot revoke a capability |

When a rule belongs to one of the lower two rows, this file gets **one line naming
the mechanism and why it exists** — not the rule itself. "`scripts/verify.sh` runs
in CI and fails on a copied `AGENTS.md`; it exists because a copy diverges silently"
is worth its tokens. "Always run `verify.sh`" is not: it is the same instruction the
mechanism already enforces, in the one place that cannot enforce it.

Say which row you put a rule in when the user asked for something this file cannot
enforce. A rule the user believes is guarded, and is not, is the expensive outcome.

## Maintenance

When an agent gets something wrong that this file should have prevented, the fix
is a new line in the Constraints or Conventions section, added at that moment.
A folder `CLAUDE.md` is worth what its last correction added to it.

**Audit both directions**, because rules drift apart from practice in two ways and
only one of them is visible:

- **Written but never enforced.** Pick a rule and ask when it last changed an
  outcome. If nothing would have gone differently without it, it is decoration —
  either move it to a mechanism from the table above, or delete it. Leaving it
  lends the whole file a credibility it has not earned, and the next reader cannot
  tell which lines are load-bearing.
- **Practised but never written.** Anything the folder's regulars do by habit and
  no line records is what the next agent will get wrong. That is the same gap the
  correction rule above closes, found before it costs something rather than after.

Neither direction is hypothetical. A rule can outlive the mechanism it describes
and read as true for months: the check it names gets renamed, or starts passing
unconditionally, and the line still says it is enforced.

---

Adapted for this repo from `skills/skill-authoring/folder-specific-claude-and-agents-md`
in [davidondrej/skills](https://github.com/davidondrej/skills) (MIT, © 2026 David
Ondrej — see `LICENSE-upstream`). Changes: personal paths and commit conventions
removed, interactive confirmation steps made conditional so the skill completes
without a user present, and a verification script added.
