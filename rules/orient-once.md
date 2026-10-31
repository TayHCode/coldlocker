# Orient once

Orientation is reading the same files to learn repo state, rules, open pull
requests and the next step. If you orient the same way twice, turn it into one
command that prints the answer compactly. From then on run that command first.

1. Extend the repo's existing orientation command. Create one only if none exists.
2. Print facts only: branch, head, dirty count, rules, open pull requests and the
   next step. Say `unavailable` for a source you could not read; never guess.
3. Measure before and after: calls and tokens. Count the files and commands you
   used to orient, then the command's output. When no tokenizer is available,
   estimate tokens as characters divided by 4 and say so.
4. Report both numbers with the command.

Example, this repository: `python3 hub.py orient` (`orient.py`). Measured
2026-10-31, estimated tokens: 10 reads (README, CONTRIBUTING, 5 rules, git status,
git log, open PR list), about 5,400 tokens, became 1 command, about 80 tokens.
