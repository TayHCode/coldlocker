# Hooks

Optional Claude Code and Codex hooks from the owner policy pack. Public exports
omit the pack, so these commands need a checkout that includes it. The catalog,
import and connect steps in the [README](../README.md) don't need any of them.

## Known gaps

Some catalog skills cite policy rules that public exports omit. `verify-loop`
cites `rules/verify-before-merge.md`. `create-verification-skill`,
`maintain-verification-skill` and `principle-prove-it-works` cite
`rules/shared-policy.md`. These skills cite `rules/model-routing.md` in their
`PACKAGE.md`, instructions or playbooks: `architect`, `arena`, `babysit-pr`, `benchmark-checklist`, `figure-it-out`, `file-pr`, `full-send`, `full-stop`, `hill-climb`, `how`, `interrogate`, `leaving-pr-comments`, `no-comments`, `p3-mode`, `poteto-mode`, `principle-attack-the-premise`, `principle-boundary-discipline`, `principle-build-the-lever`, `principle-encode-lessons-in-structure`, `principle-exhaust-the-design-space`, `principle-experience-first`, `principle-explain-the-number`, `principle-fix-root-causes`, `principle-foundational-thinking`, `principle-guard-the-context-window`, `principle-laziness-protocol`, `principle-make-operations-idempotent`, `principle-migrate-callers-then-delete-legacy-apis`, `principle-minimize-reader-load`, `principle-model-the-domain`, `principle-never-block-on-the-human`, `principle-outcome-oriented-execution`, `principle-redesign-from-first-principles`, `principle-separate-before-serializing-shared-state`, `principle-sequence-verifiable-units`, `principle-subtract-before-you-add`, `principle-type-system-discipline`, `reflect`, `setup-p3`, `show-me-your-work`, `sol-loop`, `swarm`, `tdd`, `technical-writing`, `typescript-best-practices`, `unslop`, `verify-loop`, `why`.
Without the pack, apply your own project's verification and model-routing
policy where those skills name a missing rule.

## Not a security boundary

The owner, publication and dispatch guards are a best-effort command filter, not
a security boundary. They read the text of a tool call before it runs, as the
same user the agent runs as, so one allowed call can switch them off or go
around them. A real boundary has to sit outside the agent's user, for example
GitHub credentials that cannot change a repository's visibility or push to a
public repository.

The guards do not cover:

- Edits to the hook itself: the client settings that register it
  (`~/.claude/settings.json`, `~/.codex/hooks.json`), the hook's source files,
  or switching the branch of the checkout it runs from.
- Script files. `bash script.sh` or `python3 script.py` runs a file the guards
  never read.
- Interpreter code (`python3 -c`, an awk program) that builds a path or command
  at run time.
- Publishing commands other than `git push`, `gh pr create`, `gh release`,
  `gh repo create` or `edit` with public visibility, and `gh api` calls that send
  a body or a method other than GET or HEAD. For example `npm publish`,
  `twine upload`, `gh gist create --public`, `make release` or `curl` to the
  GitHub API.
- git or gh run under another name: a shell alias or function (`alias g=git`),
  a symlink or copy with a different name, or a dashed git program such as
  `/usr/lib/git-core/git-push`. A path to git (`/usr/bin/git`), `command git`,
  `\git`, quotes and the wrappers `env`, `sudo`, `nice`, `timeout` and the like
  are read as git.
- A variable or command substitution that names git or gh where git or gh is
  only an argument of another program, followed by an alias name rather than a
  built-in subcommand: `G=git; flock /tmp/lock $G myalias`, or a glob with no
  letter such as `???` there. The same words followed by a built-in subcommand
  (`$G push`, `??? push`) are denied.
- Tools other than shell commands. The publication guard reads only Claude
  `Bash` and Codex `exec_command`, so an MCP or other tool that changes a
  repository is not checked.
- Sessions in chat mode (`chat:`), where only the owner guard runs, and guards
  the owner switched off in `~/.coldlocker/guards.json`.
- Clients, scripts, CI jobs and scheduled tasks that don't load the hook.

## Update self-test

`python3 hub.py update` ([update the checkout](cli.md#update-the-checkout)) runs
the new `hooks/coldlocker_hook.py` with an empty temporary home, so every guard
is on and your guard settings are never read or written. A write to the owner
state folder, a push mixed with other commands and a `delegate_task` call
without a `coldlocker-role` line must each be denied by their own guard (a
generic fail-closed denial does not count), `git status` must be allowed, and
the hook must write nothing to that home. A failure rolls the update back.

## What each hook needs

When `<HOOK_ROOT>` is this Coldlocker checkout, the dispatch guard resolves each
dispatch with `python3 hub.py model target` and the `models.yaml` it was given. Any
other `<HOOK_ROOT>` must hold a `cold` resolver, and without one every dispatch is
denied. The drift check still needs a `cold` CLI on PATH.

| Hook or guard | Installed by | Model resolver |
| --- | --- | --- |
| Keep-going Stop hook | `hooks/install.py` | None |
| Auto-pick PreToolUse hook | your client settings | None |
| Recall hook | `hooks/install.py --recall-command` | None |
| Coldlocker hook: briefing reminders | `hooks/install.py --hook-root` | None |
| Coldlocker hook: owner guard | `hooks/install.py --hook-root` | None |
| Coldlocker hook: publication guard (uses `git` and `gh`) | `hooks/install.py --hook-root` | None |
| Coldlocker hook: dispatch guard | `hooks/install.py --hook-root` | `python3 hub.py model target` when `<HOOK_ROOT>` is this checkout, else `<HOOK_ROOT>/cold`; both need a `models.yaml` |
| Drift-check Stop hook | your client settings | `cold` on PATH |

`--hook-root` installs all four Coldlocker hook parts together. If a `models.yaml`
(beside the `--hook-briefing` file or in `<HOOK_ROOT>`) is missing, or `<HOOK_ROOT>/cold`
is missing from a `<HOOK_ROOT>` other than this checkout, the installer prints one
`install refused` line naming it and writes nothing.

`python3 hub.py model get <role>` prints a role's configured provider, model and
effort from this checkout's `models.yaml` (`--models <file>` for another one), and
`python3 hub.py model target <role>` prints the explicit dispatch target. Review
roles require at least one repeated `--builder-model` for `target`. `reviewer`
and `reviewer-for-codex` both select by builder family. Claude builders use the
configured `reviewer` route; Codex builders use `reviewer-for-codex`. Mixed Claude
and Codex builders return INCOMPLETE. Every selected model must still differ
from every builder. Other review roles (`auditor/verifier`, `auditor-high-stakes`,
`gate-verifier`, `gate-high-risk`) select the first independent route in their
review chain. The command reads no usage telemetry. A missing file, unknown role
or unusable route prints `INCOMPLETE: <reason>` and exits 1.

## Auto-pick PreToolUse hook

`hooks/auto_pick.py` is a Claude Code PreToolUse hook for `AskUserQuestion`. When
every question has exactly one option marked `(Recommended)`, it refuses the
question and tells the agent to choose that option and continue. Questions that
match a keyword list for spending money, publishing, deleting or messaging people
still reach you. The list is a best-effort filter, so the refusal also tells the
agent to ask in plain text when its choice does one of those things. Add it
to `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [
  {"type": "command", "command": "python3 -I /path/to/coldlocker/hooks/auto_pick.py", "timeout": 10}]}]}}
```

Codex asks in plain text, so this hook does not apply there.

## Drift-check Stop hook

`hooks/drift_check.py` is a Stop and SubagentStop hook. When the branch has changes
since `origin/HEAD`, including uncommitted and untracked files, it reviews the
latest user request and diff for drift, unnecessary complexity and KISS.

The default resolves `reviewer` through the optional `cold` resolver on PATH
on each check, excluding every observed builder model. Builder identities come
from the hook payload's `model`, Claude assistant messages and Codex turn contexts.
For transcripts without model metadata, set `COLDLOCKER_DRIFT_BUILDER_MODELS`
to the actual builder IDs separated by spaces. Include any builders whose work
is absent from the transcript. Missing identities or routing are INCOMPLETE.
The resolved Codex model and effort run with a read-only sandbox. Claude routes
run with safe mode, restricted mode, tools disabled, no MCP servers and no session
persistence. Both adapters require a valid OK or DRIFT verdict; Claude also
requires a successful structured result. Other providers remain INCOMPLETE.

One review normally suffices. The same goal, diff, route, builders and command
reuse their result. Changed work after real findings gets another review, with
no round limit or automatic approval. A changed goal or reviewer starts a new
review. Route resolution still runs before reuse, so routing failure blocks even
previously approved work.

Add it to your host's supported Stop and SubagentStop configuration. This Claude
example only documents registration and does not install it:

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 180,
  "command": "python3 -I /path/to/coldlocker/hooks/drift_check.py"}]}],
  "SubagentStop": [{"hooks": [{"type": "command", "timeout": 180,
  "command": "python3 -I /path/to/coldlocker/hooks/drift_check.py"}]}]}}
```

Existing host adapters can set `COLDLOCKER_DRIFT_CMD` to an argument-parsed
command. It receives the prompt on stdin, `COLDLOCKER_DRIFT_ROUTE` as JSON and
`COLDLOCKER_DRIFT_BUILDER_MODELS` as space-separated exclusions. It must invoke
the resolved provider, model and effort. It prints exactly `OK`, or `DRIFT` on
the first line followed by specific findings. The former
`COLDLOCKER_DRIFT_MODEL` shortcut remains unsupported.

Missing tools, launch errors, timeouts, nonzero exits, invalid verdicts and empty
DRIFT findings block as INCOMPLETE. Reports omit raw provider output and command
arguments. Routing has a 10-second deadline. Reviewer execution has a 120-second
deadline. On POSIX it kills the reviewer process group. Wrappers must keep
provider children in that group. Windows uses `taskkill /T` with a further
10-second deadline. Keep the host timeout above those deadlines, as in the
180-second example. The [model-routing reference](../rules/model-routing.md) owns
the shared routing instructions.

## Keep-going Stop hook

`hooks/keep_going.py` is a Stop hook for Claude Code and Codex. After a turn that
used tools, it asks the agent to continue unless its final message ends with
`Goal:`, `Done condition:`, `Evidence:`, `Remaining:` and `Status: DONE` (with
`Remaining: none`) or `Status: BLOCKED`. `Status: PARTIAL` is never accepted.
Chat-only turns are not affected.

`python3 hooks/install.py` prints a safe summary of the change to
`~/.claude/settings.json` and `~/.codex/hooks.json`; add `--write` to back up
and merge it, keeping other hooks.
Codex may ask you to review the new hook before it runs. The hook expects a
status block, so don't install it where replies must be strict JSON. After moving
the checkout, remove the old Stop entry before reinstalling.

## Recall hook

To add context on each prompt and tool call, run
`python3 hooks/install.py --recall-command '<RECALL_COMMAND>'` to preview, then
add `--write` to install. The installer adds the command to `UserPromptSubmit`
and `PreToolUse` in both client files with a 10-second timeout, using the same
backups and atomic writes as the Stop hook.

The command reads the agent's hook event JSON on stdin. It can write Claude-style
hook JSON with `hookSpecificOutput.additionalContext` on stdout, and must exit 0
quickly. Coldlocker installs the command directly without a wrapper.

Private `*.recall-receipt.json` files beside the client files track exact owned
commands. Reinstall with a new command to replace the previous recall hook while
preserving unrelated hooks. Without `--recall-command`, recall hooks and receipts
stay unchanged.

## Coldlocker hook

`hooks/coldlocker_hook.py --root <HOOK_ROOT>` reads the Coldlocker briefing and live routing.
Coldlocker ships its own `BRIEFING.md`; use the optional `--briefing <PATH>` to
read it while keeping routing and skills under `<HOOK_ROOT>`.
On session and subagent start it injects the top lessons from `<LESSONS>/briefing.md`
when a lessons folder is set with `--lessons <LESSONS>` (install with
`--hook-lessons <LESSONS>`) or the `COLDLOCKER_LESSONS` environment variable,
which wins. With neither set it injects no lessons. `hub.py doctor` and a successful
`hub.py update` warn for each installed client hook missing this configuration,
with the reinstall flag to fix it. The warning does not fail either command. If a set folder's
`briefing.md` is unreadable or empty it says so and names the last repo copy
instead of injecting nothing. Lessons over 8,000 characters are cut at the last
whole line before that point, followed by a note with the full size and the file
to read, so Coldlocker's own context stays inline in Claude.
Coldlocker only delivers lessons; it does not check them. Coldverify owns the
cross-repo lesson checks: its fast lane fails a pull request that adds conflict
markers (L13) or lacks `/.worktrees/` in `.gitignore` (L14) in every repository
whose CI runs the repository's verify command, including this one. The proven lessons and
their checks live in the lesson library (`library/<id>/check.py`), which
leaves a lesson marked `status: enforced` out of `briefing.md`.
On session start it also checks the git checkout that holds the hook (override
with `COLDLOCKER_CHECKOUT`). If that checkout is off `main` or behind its local
`origin/main` ref, it says so and gives the `git -C <dir> pull --ff-only`
command. It never fetches, and each git call times out after 2 seconds.
To preview installation, run
`python3 hooks/install.py --hook-root <HOOK_ROOT>`; add `--write` to apply.
Use `--hook-briefing <PATH>` with `--hook-root` to select the briefing during installation.
It installs on `SessionStart`, `SubagentStart`, `UserPromptSubmit` and `PreToolUse`
in both clients with a 30-second timeout. Session events inject the session block
(or the kiss block as a fallback) and file pointers. Prompts receive the kiss
block and completion reminder. Tool calls enforce publication approvals and
routed dispatch. A Claude `Agent` or `Task` call may name its model by the tool's
alias (`haiku`, `sonnet`, `opus` or `fable`) only when the routed Claude model ID
belongs to that family; a missing model or another family is denied.
`gh pr create` (or `gh pr new`) is denied while the signed-in `gh` account already has an
open pull request on the target repository; repositories the account owns are exempt. The target is
the `--repo` value, or else the repository `gh repo view` resolves in the working folder. If the guard
can't list the account's open pull requests or resolve the target, it denies the call. It also denies
`gh pr create` in a command that names `GH_REPO`, `GH_TOKEN`, `GITHUB_TOKEN`, `GH_HOST`, `GH_CONFIG_DIR`,
`GIT_DIR`, `GIT_WORK_TREE`, `ssh` or `gh auth switch`/`login`, and one without `--repo` after `cd`, `pushd`
or `popd`, because the account or repository it checks could differ from the one used.
One call may create at most one pull request per upstream repository.
A private push is also denied when the commits it sends (those not yet on any
remote-tracking ref, merges compared with their first parent; every local branch
and tag when the push names no refspec, or every local ref when a
`remote.*.push` or `remote.*.mirror` is configured) add a
token-shaped value that `checks/scan.py` recognizes. The reason names the
pattern, file and line, never the value. A scan that fails, or whose git log and
pattern match together take over 10 seconds, denies the push; the deadline is
checked between pattern passes, so one pass can run over it. This runs
before CI; Coldverify's secret scan in CI still checks every pull request.
Private pushes may include quiet, delete, force and bare force-with-lease flags,
or appear once in a compound command. Compound pushes require verified PRIVATE
visibility, no command substitutions or environment changes, and at most one
leading `cd` to a literal absolute path. Companion commands are limited to
`true`, `false`, `:`, `echo`, `pwd`, `cat`, `head`, `tail` and `wc`. Run commits
and other Git operations in separate calls, because their hooks and configuration
can change the destination before a push. Public pushes require a standalone
`git push --force-with-lease=refs/heads/<branch>:<expected_public_head> <remote> <sha>:refs/heads/<branch>`
(an empty `<expected_public_head>` for a new branch), where `<sha>` is the full
40-character commit, and a Coldrelease PASS in the folder
`~/.local/state/coldlocker/release-approvals/<owner>__<repo>/<sha>/`. Copy the run's `approval.json`
and its export attempt's `receipt.json` there; no signature is needed. The guard checks
the binding itself: the approval's target must name the GitHub repository the remote
points at, its ref the pushed branch, and its `receipt_sha256` the receipt. The receipt
must be PASS with no reasons, match its own hash, name the tree of `<sha>` and be at
most 7 days old. On a new branch `<sha>` must be the receipt's export commit with no
parent (a fresh-history first release). On an existing branch `<sha>` must append to
the approval's `expected_public_head`: either the export commit with that head added
as its only parent (byte for byte what `coldrelease publish` builds), or an export
commit whose only parent is already that head. A parentless commit never replaces an
existing branch, so a public push is never a force-push. The lease must name
`expected_public_head`, and the target branch must still be there, so the server
refuses the push if the branch moved. A commit goes public to a repository and ref
once only: the guard records each allowed repository, ref and commit under
`~/.coldlocker/release-used` when it allows the call, so editing the approval files
cannot reuse them. The guard runs before the push, so the approval stays used even if a later hook
or prompt denies the call, the push fails, the branch is rolled back or it is deleted.
To retry, export again so Coldrelease makes a new commit. Anything missing, stale, mismatched or unverifiable
denies. The owner can say no in `~/.coldlocker/guards.json` with `"public_push": false`,
under `default` for every repository or under `repos` for the `owner/repo` the push
goes to; a `repos` entry overrides `default`. Without the setting the answer is yes.
An invalid or symlinked file denies every public push. `coldrelease publish` and
`coldrelease ship` push the release commit themselves; the guard does not treat them
as pushes. Private pushes require verified PRIVATE visibility.
Public pushes with `push.followTags` enabled are denied. Guard errors deny tool calls.

A public release is made with exactly this command, after the gated push of `<full 40-hex sha>`:

```sh
gh release create <tag> --repo github.com/<owner>/<repo> --target <full 40-hex sha> --generate-notes
```

`gh release` commands that change a release (anything but `list`, `view`,
`download`, `verify` and `verify-asset`) are publication commands. On a
repository the guard verifies as PRIVATE they run as before, as long as the
command is one plain `gh release` call (no quotes, expansions, wrappers or other
commands) that names the repository once as `github.com/<owner>/<repo>` with
`--repo` or `-R`, not straight after another option and not after `--`. The
host is spelled out so that a gh host set in the environment or gh config cannot
send the call elsewhere. On any other repository the only allowed release is the
standalone command
`gh release create <tag> --repo github.com/<owner>/<repo> --target <sha>`, where
`<sha>` is the full 40-character commit, optionally followed by
` --generate-notes`, spelled exactly so with single spaces. It is allowed when
the owner allows public pushes to that repository (see below), `<tag>` is not a
tag on GitHub yet, and `<sha>` is the current head of a branch on GitHub that
the guard itself let through as a Coldrelease PASS push of that commit to that
repository and branch (its record in `~/.coldlocker/release-used`). The target
is a commit, not a branch name, so the tag lands on the gated commit even if the
branch moves after the check; a record for another commit, branch or repository
does not count. If someone creates the same tag between the check and the call,
GitHub attaches the release to that tag instead. Release notes come only from GitHub (`--generate-notes`) or are empty:
`--notes` and `--notes-file` put unscanned text on a public page. Release assets
(`gh release upload`, or files after the tag) are denied on public repositories;
they need their own reviewed shape that runs the secret scan on each file first.
An owner yes does not cover `gh release`.
Unsupported executable expansions and shell scripts on stdin are denied. Script
files are outside this conservative command guard's coverage.
Direct `gh api` requests with bodies or methods other than GET and HEAD are denied.
Pushes that can recursively publish submodules are denied for both private and public parents.
Stop remains covered by the keep-going hook.

The publication guard reads no git or gh config. It denies `git <word>` and
`gh <word>` when the word is not in the built-in sets `GIT_BUILTINS` and
`GH_BUILTINS` in `hooks/publication_guard.py`, so an alias or extension from
git config, gh config, `-c`, `GIT_CONFIG_*` or an include never runs through
it. It does the same where `git` or `gh` appears as a word inside another
command, so `which git gh` is denied; rephrase so `git` is not followed by a
bare word. Where a word in that position is a glob or brace that can expand to
`git` or `gh` (`gi[t]`, `g?t`, `[^a]it`, `{git,}`, `{f..h}it`), the command is
denied too, because the guard cannot see which program runs; a pattern with no
letter (`???`, `dir/*`) counts only when a built-in git or gh subcommand follows
it, and so does a variable or command substitution (`$G push`). The guard
decodes `$'...'` and `$"..."` quoting before reading words (`$'\147it'` is git),
and denies a bash extended glob such as `g?(x)it` anywhere in a command. The sets leave
out every name an alias can shadow (deprecated git commands such as
`whatchanged`, and gh's `co` and `help`), and `git send-pack` and `git
http-push`, which push without the word `push`.

A built-in that a newer git or gh adds is denied until it joins the set. To add
one, run `python3 tests/builtin_probe.py` with that git or gh installed and copy
the names it prints into the set in a reviewed PR, but only names built in on
every host that runs the hook: on a host whose git or gh lacks a name, an alias
of that name runs. The sets currently hold names built in since git 2.43 and on
the gh of the CI runner. The probe tries each name
under an alias and prints only the names whose real command still runs. The hook
tests fail when a name in either set can be shadowed by an alias on the host
that runs them.

To run other rule hooks through the Coldlocker hook instead of registering them
separately, add `--hook-run 'CLIENT:EVENT[,EVENT]=COMMAND'` (repeatable;
CLIENT is `claude` or `codex`). Each command gets the event JSON on stdin and
runs only on its listed events. Its `additionalContext` (or plain stdout) is
appended, and a `deny` from it blocks the tool call. A failing command is skipped.
Claude Code inlines at most 10,000 characters of one hook's context and hides the
rest behind a file preview. In Claude hooks (detected by `CLAUDE_PROJECT_DIR`), when the merged context is over that limit, Coldlocker's own
briefing and lessons stay inline with the chained outputs that fit, and the rest are saved
together to one owner-only file under `~/.cache/coldlocker/hook-output/` behind a
one-line pointer. If that file cannot be written, or Coldlocker's own context leaves no room
for the pointer, everything is returned inline as before and Claude keeps it in its own file.
Chained commands share a 20-second budget; a tool call whose chained command
cannot start or finish within it is denied. A chained command that was also
registered on its own (for example an earlier `--recall-command`) is removed
from the events it is chained on, so it runs there only through the chain. Changing an installed
chain (or its root) needs `--remove-command '<EXACT_OLD_HOOK_COMMAND>'` in the
same reinstall, so chained guards are never dropped silently.
A chained command that times out is killed with its whole process tree.

To give agents a repository's proven lessons automatically, chain
Coldrecall on session and subagent start, bounded to a few facts:
`--hook-run 'CLIENT:SessionStart,SubagentStart=python3 "<LESSON_TOOLS>/coldrecall.py" hook --budget 5'`.
It reads the repository's compiled `.coldrecall/reflex.json` (or the main
checkout's, from a linked worktree) and prints nothing when there is none.

Start a message with `chat:` to switch one session to chat mode. The Coldlocker hook,
every chained command and the keep-going hook then do nothing for that session,
the publication and dispatch guards included (the owner guard still runs), and the agent is told to ignore the rules injected earlier.
Start a message with `work:` to switch back. The switch is a file at
`~/.coldlocker/chat/<session_id>`, so it doesn't affect other sessions.

To start sessions without development context, launch the client with
`COLDLOCKER_START_IN_CHAT=1` in its environment (for example
`COLDLOCKER_START_IN_CHAT=1 claude`). SessionStart, SubagentStart and
UserPromptSubmit then inject nothing: no briefing, lessons, footer or chained
output. Every guard still runs as in a normal session. Only a typed `chat:` turns
the publication and dispatch guards off. Only the value `1` turns it on.

### Owner guard switches

The owner turns guards on or off in `~/.coldlocker/guards.json`. The guards are
`publication`, `dispatch` (routing check), `reminders` (injected briefing),
`keep_going` and `public_push` (the owner's yes or no to public pushes; see above). `default` applies everywhere, and `repos` overrides it for a
GitHub `owner/repo` (the literal `origin` URL in the local git config of the session's working directory, without `insteadOf` rewrites, includes or `GIT_*` environment overrides):

```json
{"default": {"keep_going": true},
 "repos": {"example-org/example-repo": {"keep_going": false, "reminders": false}}}
```

#### Saying no to public pushes

A public push that passed Coldrelease is allowed unless the owner says no. To say no
everywhere, set `{"default": {"public_push": false}}`. To say no for one repository,
set `{"repos": {"owner/repo": {"public_push": false}}}`. That key is the repository the
push goes to, not the session's `origin`. A `repos` entry overrides `default`. To say
yes again, delete the key or set it to `true`. When the setting is absent, the answer
is yes; an invalid or symlinked file means no to every public push.

#### The owner's yes for one command

Moving a repository or checkout to its private twin needs no yes: an exact
`git [-C <dir>] remote set-url origin <url>` whose new GitHub repo is the current
origin's `owner/name-private`, or `gh repo rename <name>-private --repo owner/<name> [--yes]`.

The same move on another host is one fixed shape (`SSH_REPOINT` in `hooks/owner_rules.py`), matched
in full: `ssh <host> git -C <path> remote set-url origin <url>`, single spaces, nothing before or
after. The host is a plain name (no user, no options), the path is plain (an optional leading `~/`
or `/`; each part is letters, digits, `_`, `-` and `.`, may start with one `.` but not with `-`, and does not end with `.`; no
`..`, no empty part, no trailing `/`, and nothing the owner guard protects, such as `.git` or
`.coldlocker`), and the URL is a GitHub https, ssh or `git@` URL of
`<owner>/<name>-private`. The guard then makes one ssh call of its own,
`ssh -T -o BatchMode=yes -o ConnectTimeout=5 -- <host> 'git -C <path> config --local --no-includes --get-all remote.origin.url && git -C <path> ls-remote --get-url <url>'`
(`~/` expanded locally, as the shell does), and allows the command only when that origin is exactly
one URL in the checkout's own config, naming
`<owner>/<name>` or already `<owner>/<name>-private` and the new URL comes back unchanged (no
`insteadOf` rewrite). A different repo or owner, a rewritten URL, an ssh error, other output or no
answer within 8 seconds keeps the denial. Any other spelling or ssh-wrapped
git command is denied without the read.

A few other commands the guards deny can run once with the owner's yes. The operator records
it for the exact command text:

```sh
python3 <coldlocker>/hooks/owner_rules.py yes '<exact command>' --reason "<owner's words, time>" --cwd <folder>
```

The record (`~/.local/state/coldlocker/owner-yes/<sha256 of the command>.json`) works
once, for 60 minutes, and only for a byte-identical command run in that folder (default:
the folder the grant runs in). Only these shapes qualify, matched in full, where a name is
letters, digits, `_` and `-`, not starting with `-`, with single dots between them:

- `gh api -X DELETE repos/<owner>/<repo>/branches/<branch>/protection/enforce_admins`
- `gh repo create <owner>/<repo> --public`
- `git push --force-with-lease=refs/heads/<branch>:<40-hex> origin <40-hex>:refs/heads/<branch>`
  (the same branch both times)
- `printf '%s\n' <64 lowercase hex> > ~/.coldlocker/release-denylist.sha256`, only when the
  hex is the sha256 of `~/.config/coldrelease/denylist.json` at that moment and the target is
  absent or a regular file with one link. This is the only write to `~/.coldlocker` a yes allows.

Any other command, including a different spelling of one of these, gets no yes. New shapes
are added only by a reviewed pull request to `YES_SHAPES` in `hooks/owner_rules.py`. Every grant and use is
appended to `~/.local/state/coldlocker/owner-yes.log`. A record never allows any other write to
`~/.coldlocker`, an unreadable command, or a push that skips its checks: a push still
needs the secret scan, and a public push still needs its Coldrelease PASS, lease and
release-used check (a yes only lets an approved parentless export replace the public
head). `"public_push": false` for `default`, the working directory's repo or any repo the
command names makes every record unusable, except the checksum write, which pushes nothing. The record matches text, not the environment, so a command that takes its target from
an inherited variable such as `GH_REPO` acts on whatever that variable says when it runs. This stops accidents and unrequested changes
and leaves an audit line; it does not stop an agent that claims a yes it never got.

Outside a git repo or without an origin, only `default` applies; if git errors inside a repo
or origin has several URLs, every guard stays on.
Edits take effect on the next hook call. A missing file means every guard is on. After upgrading, rerun
the installer once with `--hook-root` so the Stop hook gets `--owner-rules`. Until then `keep_going: false`
only drops the prompt's completion reminder; the Stop hook keeps blocking, and its block message states
the required Goal:/Done condition:/Evidence:/Remaining:/Status: ending.
The file is read only where the Coldlocker hook (installed with `--hook-root` by the same Python) is present, because
that hook keeps agents out of it; otherwise the Stop hook ignores it and every guard stays on.
If the file is invalid, a symlink or not a regular file, or if it exists and git errors, every guard stays on and the error appears in the prompt
context (silent in chat mode) and in deny reasons. Agent edits to `~/.coldlocker`, tool calls that run inside it, non-delegation tool calls whose input names it or
`guards.json`, and changes to a repo's `origin` remote (including `git remote` commands that use shell
expansion and the `git config` `set`, `unset`, `rename-section` and `remove-section` subcommands; a rename checks both section names) or paths into git metadata (`.git`) are always denied (chat mode included). Git commands that write other metadata (commit, `git config user.*`)
stay allowed. Read-only tools (Read, Glob, Grep, LS) and delegation prompts are not checked. File tools (Write, Edit,
MultiEdit, NotebookEdit), WebSearch, WebFetch and T3 `html_render`/`html_preview` are checked only through their
path fields (`file_path`, `notebook_path`, `path`, `cwd`, `workdir`), and Codex `apply_patch` only through its
`*** Add/Update/Delete File:` and `*** Move to:` paths (a patch without any is checked as text). These paths are
resolved through `..` and symlinks, with a leading `~` checked both expanded and as a literal folder name, and with tabs and line breaks
also removed (Codex drops them, openai/codex#52688); text in
content fields is not checked. A Bash, Monitor or Codex `exec_command` command is checked only at the write
targets the guard can parse: redirect targets; the operands of writing programs (`rm`, `mv`, `cp`, `ln`, `touch`,
`mkdir`, `chmod`, `tee`, `dd of=`, `sed -i`, `sort -o`, `find -delete` or `-exec`, `xargs` running one of these,
git commands that write a path); every operand of a program it does not know; and values assigned in a command
prefix. Scripts that run elsewhere are checked the same way: `sh -c`, `su -c`, `ssh`, `sudo`, `env`, `setsid`,
`systemd-run`, a launcher's command after `--`, `$(...)`, a heredoc fed to a shell, and a script the same command
wrote and then runs (with a heredoc, `cat`, `tee`, `printf '%s\n'` or `echo`, appends included; a written script
whose text it cannot read counts as a command it cannot read). `cp`, `mv`, `install` and `ln` targets include
`-t` and `--target-directory` folders, every `install -d` operand is a target, `git checkout` and `git restore`
paths are checked like an unknown program's operands, an `ln` source is checked like an unknown program's operand, `git clean`
paths are write targets, the `-C` and `--work-tree` folders of a git command that writes the worktree are write
targets (without them, its working folder is checked for `~/.coldlocker` and git metadata), the `-C` and `--git-dir` folders of a `git config` or `git remote` write are checked like an unknown program's operands,
`find -delete` or `-exec` without a start folder targets the cwd, the `-name` and `-path` values of a `find` that
deletes or runs a command are checked like operands, and `rsync --remove-source-files` sources are write targets. An unknown program's plain
operand (no space or glob) that points into git metadata is denied. Targets are resolved as paths (after `$HOME`, brace (lists and `{a..z}` ranges) and glob
expansion, `for` loop values and earlier assignments, against the session cwd and any `cd`, `pushd` or `-C`
directory). Sed scripts, regexes, read-only commands and heredoc bodies are text. The home directory or a folder
above it (`/`, `~`, `$HOME`) is denied only as such a target. Interpreter code (`python3 -c`, an awk program, a
heredoc fed to an interpreter) is checked only for owner-state path pieces and `remote.` or
`git remote … origin` text. `gh repo rename` and `gh repo create` with `--source` or `--remote` (with or without `-R`/`--repo`) count as origin
changes. After a `PATH` change or `source`, every program's operands are checked as if the program were unknown.
A command whose targets the guard cannot pin down (a non-literal write target, a relative write after a `cd` to a
variable, an `xargs` or `find` action it does not know, `git config` with `--git-dir=`, more than 64 `for` loop combinations for one target (values of every loop that reuses a variable count), a prefix such as `GIT_DIR=` or `LD_PRELOAD=`, a
`\r`, or syntax the parser rejects) falls back to the older check: every word of its text is resolved as a path,
and a pattern with more than 4096 expansions is denied. This is a hook heuristic, not a sandbox: interpreter code
can still reach any path.
Each owner-guard denial ends with what matched: the token, path field or text, and the path it resolved to
(for example `token '/', which resolves to / (a folder that holds ~/.coldlocker)`). A publication-guard denial
for a command it can't read names the program or syntax (`'xargs' runs commands it cannot read`) instead of
calling it a public publication. Option values, unknown `gh api` methods and expanded program or subcommand words
are never shown; URL credentials and known token formats are masked, and long tokens are cut to 120 characters.
The publication guard checks the script that `sh`, `bash` or `dash` reads from a visible heredoc (a quoted or
escaped delimiter, or an unquoted one whose body has no `\`, `$` or backtick) like any other command. Because
the script's commands share its stdin, every line before the last may only run `cd`, `echo`, `printf`, `set`,
`export`, `unset`, `pwd`, `test`, `true` or `false`, and the script may not name `/proc/` or a `/dev/` path other
than `/dev/null`; otherwise a command could read or rewrite the lines still to run. A shell
that reads its script from anything else on stdin, from `/dev/stdin` or from a command substitution is denied, and
so is `source` or `.` of either. `xargs` is allowed only when it runs a read-only program (`wc`, `grep`, `cat`,
`head`, `ls`, `stat`, …) with options it knows.

Private `*.coldlocker-receipt.json` files use the same ownership mechanism as recall.
Reinstalling with a new root replaces the previous Coldlocker command and preserves
unrelated hooks. To remove retired handlers from every event, preview
`python3 hooks/install.py --remove-command '<EXACT_OLD_COMMAND>'`, then add
`--write`. Repeat `--remove-command` for multiple commands. Removal matches the
whole command, uses backups and atomic writes, and preserves other handlers.

## Decision log

Each PreToolUse decision appends metadata to `~/.local/state/coldlocker/decisions.jsonl`
(directory mode 0700, file mode 0600). Records contain UTC time, client, session and
tool-use IDs, tool name, working directory, verdict, and a guard and safe reason
summary for denials. Commands and tool inputs are never recorded. Reasons use
fixed summaries because owner matches, resolver errors and chained hook messages
can contain private input. Transcript IDs retain the link to the full reason.
The file rotates to `decisions.jsonl.1`, replacing the previous archive, before
an append when it exceeds 10 MiB. A mode-0600 `decisions.lock` serializes rotation
and append across processes on Linux and Windows. Lock waits are capped at 250 ms
for the hook; a busy lock drops that log entry. The verdict is printed and flushed before logging; logging exceptions leave it unchanged.
`python3 hub.py guard-stats --days 7` snapshots both files under the same lock and reports daily and total
calls, denials by guard, denial rates and the ten most common reasons, with quoted
tokens replaced by `…`. Missing logs print `no decisions recorded` and exit 0.

## Scoped transcript advice and startup budget

SessionStart and SubagentStart filter the shared lesson briefing using each
`L<n>-*.md` file's existing frontmatter. Model-judged transcript lessons are
advisory. They appear only when `repos` contains the session's repository name
or origin owner/name, compared without case. Git's common directory identifies
the repository across worktrees and subdirectories when no origin identity is
available. Origin takes precedence over the local checkout name. Ambiguous bare
or separate Git directories without origin exclude advisory advice. Missing
repository identity, missing metadata, and empty `repos` do not authorize transcript advice. A global
scope alone does not authorize judged advice. Matching advice is labeled
`advisory, transcript-only`. Exact `verification: proven` lessons retain their
existing delivery regardless of `repos`; legacy transcript judge descriptions
are still advisory. Recognized legacy non-transcript sources (`seed`,
`learning-pass`, and `owner-request`) keep their existing delivery; unknown
classification does not authorize global advice.
The shared briefing and lesson files are never rewritten by the hook.
If lesson metadata cannot be read within its shared allowance, the hook names
the omission and excludes entries whose scope and verification are unknown.

The hook reserves briefing, file references and checkout notices before fitting
lessons into the remaining 9,600-character startup allowance. The existing
8,000-character lesson allowance still applies first. Lesson cuts include a
file pointer to a cached filtered copy when it fits; if no room remains, lessons
are omitted. Cache failures preserve startup policy. Briefing
and chained policy text are preserved in full even when they exceed the inline
ceiling. Chained hooks keep their existing independent spill behavior. No live hook or
lesson configuration is changed by installing this code in a review worktree.
