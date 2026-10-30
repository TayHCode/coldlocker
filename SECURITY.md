# Security

## Scope and reporting

This project is unreleased. The local Linux engineering milestone does not
establish the cross-agent product release. There is no published supported
release yet. See the [release guide](docs/release.md).

For invited private trials, report suspected vulnerabilities directly to the
repository owner in the same private conversation used to deliver the source
archive. The owner is responsible for monitoring that conversation. Include
the source revision, affected component and nonsecret reproduction steps.
Keep credentials, private catalog contents, account identifiers and raw client
configuration out of reports and public issues.

If you received a forwarded archive without a private reply channel to the
owner, ask the sender to establish that contact before starting the trial.
A public reporting channel has not been selected or verified; select one before
wider public distribution. Do not assume GitHub private vulnerability reporting
is enabled.

## Containment and recovery

If a local catalog connection is implicated, close affected client sessions
and stop the owned catalog process. Back up client configuration privately,
then remove only its `agentlocker` entry (and `agentlocker-gateway` if used),
preserving other settings. If an imported item is implicated, review it and
use `python3 hub.py remove <kind> <name>` with confirmation to remove the owned
import. Removing an item or a client entry does not revoke a provider token.

If credentials may have been exposed, use the provider's approved private
revocation flow. Never send secret values in chat. Preserve evidence privately
and follow the [private-state recovery guidance](docs/release.md#private-state-upgrade-and-rollback)
before restoring anything over live state.

The stdio catalog is read-only and does not resolve `secret://` references.
Executing imported instructions, enabling routines, using the gateway and
authorizing upstream providers require separate review. A clean current-tree
scan does not sanitize historical Git objects. Repository or history-bearing
distribution remains blocked on history review. A source-only archive must be
reviewed for its own contents, exclude private state and evidence, and receive
publication approval.
