# Verify in Coldbox

Once a server's Coldbox path is declared usable, use one spawn command to create
a VM from one approved base snapshot, verify the exact candidate with the
project's existing gates, and destroy the owned VM even on failure. Slow is fine.
Record the candidate, snapshot, command, result and cleanup evidence. Send failure
evidence to Coldfix; Coldcleaner keeps abandoned owned disks off server SSDs.
Preserve review and merge requirements. Report unavailable VMs or failed cleanup
as blockers; do not silently move workloads to the coordination host.

The current host-free planner executes nothing. Until real VM execution is
available, use the project's authorized verification path and report that gap.
Provisioning and service or schedule changes need separate authorization.
