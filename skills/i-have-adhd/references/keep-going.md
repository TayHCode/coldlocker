# Keep-going status contract

Coldlocker's `hooks/keep_going.py` checks this final response structure after
tool work. Use the exact field labels and put the status on the last line:

```text
Goal: <requested outcome>
Done condition: <observable acceptance condition>
Evidence: <observed results, commands and exit codes, and verification limits>
Remaining: none
Status: DONE
```

State the goal and done condition before acting. Continue authorized work until
the done condition is verified. Use DONE only when the evidence supports it and
nothing remains. If blocked, name the exact blocker and the action needed in
Remaining, then end with `Status: BLOCKED`. An explicit user stop wins: do no
more work and report BLOCKED if work remains. DONE and BLOCKED are the only
terminal states; PARTIAL is not a terminal state.

The hook checks structure, not the truth of evidence. Preserve the user's scope
and authorization boundaries. This reference neither installs a hook nor grants
permission to publish, deploy, merge, or change services.
