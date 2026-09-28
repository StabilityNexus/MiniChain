---
name: security-remediation
description: Close out a security review — match recent commits to each finding in an unremediated security-review report, confirm remediations with the user, collect explanations for anything left unfixed, and publish both the review and a remediation report. Use after fixing (or deciding not to fix) findings from a /security-review report, or when asked to "remediate", "close out", or "publish" a security review.
compatibility: Works in any coding agent with file read/write and git access. Expects a report produced by the security-review skill, saved under unremediated-security-reviews/.
metadata:
  version: "1.0"
  category: security
allowed-tools: Read Grep Glob Write Edit Bash(git log:*) Bash(git show:*) Bash(git diff:*) Bash(git status:*) Bash(git rev-parse:*) Bash(git remote show:*) Bash(git remote get-url:*) Bash(git mv:*) Bash(mv:*) Bash(mkdir:*) Bash(date:*)
user-invocable: true
---

# Security Remediation

## Purpose

This skill is the second half of a two-part workflow. The **security-review**
skill produces a private report of findings under
`unremediated-security-reviews/`. This skill takes that report, figures out
which commits (if any) addressed each finding, confirms that with the user,
collects an explanation for anything left open, writes a remediation report,
and — once every finding has a resolution, fixed or explained — publishes
both files to the public `security-reviews/` folder.

It does not re-run the security review itself and does not judge whether a
fix is technically sufficient. It records what was done and why, and lets
the user own that judgment.

## Step 1: Locate the Review to Close Out

1. If the user names a specific report file, use it.
2. Otherwise, list `unremediated-security-reviews/*.md`, excluding any file
   ending in `_remediations.md`. If exactly one candidate exists, use it.
   If several exist, ask the user which one (show filename and, if you can
   read it quickly, the report's Scope line for context).
3. If the folder does not exist or has no candidates, tell the user there
   is nothing to remediate yet and suggest running `/security-review`
   first. Stop.

Read the chosen report in full.

## Step 2: Parse Findings

From the report's `## Findings` section, extract each finding: number,
title, `file:line`, severity, category, description, and recommendation.

Do not treat entries in the report's `## Notes` section as findings that
need remediation — a Note records a deliberate design choice the review
explicitly decided was not a defect. Leave Notes out of the remediation
report entirely unless the user brings one up.

## Step 3: Find Candidate Remediating Commits

1. Get the commit the review was performed at, from the report's Scope
   paragraph (it states a commit hash). Call it `<review-commit>`.
2. Run `git log --oneline <review-commit>..HEAD` to see what has happened
   since. If `<review-commit>` is not an ancestor of HEAD (e.g. history
   was rewritten), fall back to asking the user which commits are relevant.
3. For each finding, narrow to commits touching its file:
   `git log --oneline <review-commit>..HEAD -- <file>`. Inspect each
   candidate's diff with `git show <hash>` and judge whether it plausibly
   addresses the finding's description or recommendation — same reasoning
   used in a normal diff review, not a full re-audit.
4. Build a per-finding candidate list (possibly empty).

## Step 4: Confirm With the User

Present your candidate matches finding-by-finding and ask the user to
confirm or correct them. For every finding, you need three things before
you can write it up:

1. Which commit(s), if any, actually remediated it.
2. Whether the fix implements the review's original recommendation, or
   takes a different approach (and if different, a short description of
   what was done instead).
3. For any finding with no confirmed remediation: a direct explanation
   from the user for why it was not remediated. Ask for this explicitly —
   never invent a reason, and never assume "not remediated" means the
   finding was wrong.

Batch this into as few questions as practical (e.g. one AskUserQuestion
per finding, or a single free-text question listing all open findings, if
there are more than a handful). Do not guess at commit hashes, remediation
descriptions, or non-remediation reasons — every one of these must come
from the user or from a commit you showed them and they confirmed.

## Step 5: Write the Remediation Report

Resolve the commit link format first: run `git remote get-url origin` (or
`git remote show origin`), normalize it to an `https://` URL (strip a
`git@host:` SSH prefix to `https://host/`, drop a trailing `.git`), and
build links as `<https-remote>/commit/<full-hash>`. If there is no remote,
list bare commit hashes instead of links and say so in Comments.

Use this exact structure:

```
# Remediations of Security Review Findings

Review date and time: <the original review's date/time, copied verbatim
from the source report's metadata>

## Remediations

### Remediation of Finding <N>: <finding's short title>

- [x] This remediation implements the security review's recommendation for this finding.
- [ ] This remediation addresses the finding in a way that differs from the security review's recommendation.

Remediation commits:
- [<short-hash>](<https-remote>/commit/<full-hash>)

Description: <what actually changed, in the user's own terms where given>

## Non-remediated findings

### Finding <N>: <finding's short title>

This finding was not remediated because <user's explanation, verbatim or
lightly cleaned up — do not soften or omit it>.

## Comments

<Optional — only include this section if the user gave you something to
put here, e.g. context that doesn't fit a single finding, or a note about
missing remote/commit info. Omit the section entirely if empty.>
```

Exactly one checkbox is checked per remediated finding — `[x]` on the one
the user confirmed, `[ ]` on the other. Never check both, never check
neither for a remediated finding.

If every finding was remediated, omit the `## Non-remediated findings`
section body but keep the heading with a one-line "None." — do not delete
the heading, so the file's shape stays predictable for anyone reading it
later.

## Step 6: Save, and Publish if Complete

1. Filename: take the source report's filename (e.g.
   `sec_review_2026-09-22T14-03-00Z.md`) and derive
   `sec_review_2026-09-22T14-03-00Z_remediations.md` — same timestamp,
   `_remediations` suffix before `.md`.
2. If **every** finding from Step 2 now has either a confirmed remediation
   or a user-provided non-remediation explanation:
   - Create `security-reviews/` at the repo root if it doesn't exist.
   - Move (not copy) both the original report and the new remediations
     file from `unremediated-security-reviews/` into `security-reviews/`,
     using `mv` (the source folder is gitignored/untracked, so `git mv`
     does not apply to the report file itself — plain `mv` is correct
     here; if the remediations file was written directly to
     `security-reviews/`, no move is needed for it).
   - Confirm neither file still exists under `unremediated-security-reviews/`
     afterward.
3. If any finding still lacks a resolution (the user wasn't ready to
   explain it yet, or remediation is still in progress):
   - Save the remediations file under `unremediated-security-reviews/`
     instead (do not publish either file).
   - Clearly list which finding(s) are still blocking publication.

## Step 7: Report to the User

Summarize: how many findings were remediated vs. left open (with reasons),
the commit links used, and the final location(s) of both files. If
publication happened, remind the user the private copies were removed and
only the public pair remains.

## Operating Rules

- This skill DOES write to the repository: the remediation report, and
  (once complete) moving both files into the tracked `security-reviews/`
  folder. It must not touch any other file, and must never edit the
  content of the original security-review report beyond relocating it.
- Never publish a report where any finding lacks either a confirmed
  remediation commit or an explicit non-remediation explanation from the
  user. Partial completion stays private.
- Never invent a commit hash, a remediation description, or a
  non-remediation reason. Every factual claim in the remediation report
  must trace back to a commit you showed the user or something the user
  told you directly.
- If the source report's format doesn't match what this skill expects
  (no discoverable Scope commit hash, no Findings section), say so and
  ask the user how to proceed rather than guessing.
