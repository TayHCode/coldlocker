# Upgrade agentlocker

agentlocker has no release numbers yet ([release status](release.md)). "A new
version" means newer commits of the source you cloned. Your library does not live
in that source, so updating it does not touch your library.

Run every command from your agentlocker checkout, the same folder you
[installed](install.md) into. The imported-item check needs only Python and
Git. The backup uses macOS and Linux commands; on Windows, use `py -3` for
`python3` and copy folders in File Explorer.

## What stays and what changes

| Stays as it is | Why |
| --- | --- |
| Skills, rules, routines and MCP definitions you imported | They live in `.hub-store/`, which Git never touches |
| Import history, secrets and routine records | Also in `.hub-store/` |
| `config.json`, if you made one | Ignored by Git |
| Agent settings (Claude, Codex, Cursor, ...) | The connect entry points at your checkout folder, and the folder does not move |
| The gateway tool install in `.scratch/` | Changes only if you rerun `gateway.py install` |

| Changes | |
| --- | --- |
| The code, the shipped skills and rules, `manifest.json`, the docs | Tracked files in the checkout |

## Upgrade

1. Look at where you are. The second command should print nothing. If it prints
   file names, you edited tracked files; see [If the pull refuses](#if-the-pull-refuses).

   ```sh
   git rev-parse --short HEAD
   git status --short
   ```

   Write down the first line. It is your way back.

2. Close agents that use agentlocker. If you run the gateway, write down the
   `--port` and `--require-token` options you start it with, then stop it.

   ```sh
   python3 gateway.py stop
   ```

3. Back up your library, outside the checkout and outside Git. The copy keeps
   the list of items you imported to compare later. If you have a `config.json`,
   copy it too. If it sets `store_path` to a folder other than `.hub-store`,
   also run `cp -a <STORE_PATH> ../agentlocker-backup/store-path`: that folder
   holds your secrets and routine records, while `.hub-store` still holds
   imported items and history.

   ```sh
   mkdir -m 700 ../agentlocker-backup
   cp -a .hub-store ../agentlocker-backup/
   ```

4. Fetch and read what is coming, then update.

   ```sh
   git fetch
   git log --oneline "HEAD..@{u}"
   git pull --ff-only
   ```

   `--ff-only` refuses to merge, so it either moves cleanly forward or stops
   without changing anything. No Git login on this machine? Use the bundle you
   were given instead: `git fetch <PATH_TO_BUNDLE> <APPROVED_BRANCH>`, then
   `git merge --ff-only FETCH_HEAD`.

5. Check it worked. Every line must start with `PASS` and the command must
   exit 0. The `git diff` command should print nothing, meaning the list of
   items you imported is the same as before. It never lists shipped items, which
   an upgrade may add or rename. If you imported nothing, that file does not
   exist and you can skip it.

   ```sh
   python3 hub.py doctor
   git diff --no-index ../agentlocker-backup/.hub-store/catalog/manifest.json .hub-store/catalog/manifest.json
   ```

   Also run `git rev-parse --short HEAD`. It should differ from step 1 and match
   the newest commit the log showed.

6. Reload your agents. Ask one to call `hub_list_skills`. You should see your
   imported skills next to the shipped ones. No `connect` step is needed.

7. Only if you use the gateway: start it again with the same `--port` and
   `--require-token` options you used before; a plain `start` resets them. If
   `start` says the runtime is missing or outdated, run `install` once, then
   `start`.

   ```sh
   python3 gateway.py start
   python3 gateway.py status
   ```

   ```sh
   python3 gateway.py install
   ```

   `install` refuses to run while the gateway is up. See
   [gateway upgrades](gateway.md#install-and-upgrade).

Optional follow-ups:

- If you linked skills into a project with `hub.py link`, preview links for
  newly shipped skills: `python3 hub.py link --target <root> --dry-run`.
- If you publish to hosted bots, publish again so they get the new shipped
  content ([self-host](self-host.md)).

## If the pull refuses

Git says `Your local changes to the following files would be overwritten`: you
changed a tracked file. Edit the originals of your skills outside this checkout
and import them again; do not edit the shipped copies. To keep what you changed,
save it, put the file back, then pull:

```sh
git diff --binary > ../agentlocker-my-edits.patch
git checkout -- <FILE>
git pull --ff-only
```

If Git says the branches have diverged, stop. Someone committed in this
checkout; ask whoever maintains it before going further.

## Roll back

Pick the smallest one that fixes your problem.

**The new code misbehaves.** Close your agents and stop the gateway first
(`python3 gateway.py stop`), because running copies keep using the new code.
Then go back to the commit you wrote down in step 1. Your library is untouched
either way, so it keeps working.

```sh
git switch --detach <COMMIT_FROM_STEP_1>
python3 hub.py doctor
```

You are now on that old commit and Git calls this "detached HEAD". It is fine
for running agentlocker; do not commit here. Reopen your agents and start the
gateway with its original options. To upgrade again later, run
`git switch <BRANCH>` (usually `main`), then `git pull --ff-only`.

**An imported item is wrong after you re-imported it.** Every update keeps the
old copy under `.hub-store/history/`. Put it back by importing that copy with the
same owner and origin that `python3 hub.py list` shows for the item:

```sh
ls .hub-store/history/skills/<NAME>/
python3 hub.py import-skill .hub-store/history/skills/<NAME>/<TIMESTAMP>/skills/<NAME> --owner <OWNER> --origin <ORIGIN>
python3 hub.py doctor
```

Other kinds use their own folder and command, such as `rules` with
`import-rule`. The restore is itself an update, so it also keeps the version
you replaced.

**A client setting is wrong.** An upgrade does not change agent settings. If a
`connect --write` earlier broke one, it printed an `Undo: cp <backup> <file>`
line and left a timestamped `.bak` next to the file; run that `cp`.

**Your library looks damaged.** Do not copy the whole backup over a live
`.hub-store/`; that can erase newer secrets, history and routine records. Keep
both copies and follow
[private state, upgrade and rollback](release.md#private-state-upgrade-and-rollback).
