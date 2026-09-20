# Inventory consolidation — open question, not yet decided

This file exists in both `ansible-stationctl` and `ansible-proxmox` (same
content, kept in sync by hand) so the question and its context aren't
lost between sessions. As of 2026-09-19, **nothing has been implemented**
— this is a proposal to evaluate, not a plan in motion.

## The problem being considered

Both repos currently maintain their own `inventory/hosts.ini`, hand-edited
per host. This repo (`ansible-stationctl`) just added
`scripts/check_ip_drift.py` to catch drift between that static file and
each host's live Tailscale IP — a real problem, since a host's IP moving
without the inventory catching up is exactly what happened to the
navidrome service that this feature was built in response to (see
`ansible-proxmox`'s memory notes).

The open question: instead of detecting drift after the fact in two
separate static files, should both repos resolve host IPs from one live,
shared source instead?

## Proposed alternative: Tailscale-based dynamic inventory

Replace static `inventory/hosts.ini` in both repos with a small dynamic
inventory script that queries `tailscale status --json` at run time.
Rationale: the entire fleet (both stationctl workstations, starhaven, and
every guest VM/LXC on it) already lives on one tailnet — Tailscale is
already the authoritative source of "what's reachable and at what IP,"
so there'd be nothing left to go stale.

**What this would NOT replace**: role/purpose metadata
(`machine_role`, `ansible-proxmox`'s `vm_categories.yml`, `host_vars/*`)
stays exactly as it is — Tailscale only knows device names and IPs, not
"this is a bare-metal dev workstation" or "this is a game server." That
sidecar-metadata pattern already exists (`vm_categories.yml`), so a
dynamic inventory would extend the same shape, not replace it.

**Tradeoffs, explicitly**:
- (+) No inventory file to ever go stale in either repo.
- (+) `check_ip_drift.py`'s preflight-and-patch dance becomes mostly
  unnecessary — the inventory would already be live.
- (−) One more moving part each repo depends on at run time (a Tailscale
  query) instead of a dumb static file anyone can eyeball or edit offline.
- (−) Still an open question whether **one shared inventory script**
  should live in a third location both repos reference, or whether **each
  repo keeps its own copy** of the same small script. A third "shared
  inventory" repo was considered and explicitly rejected as an option —
  it just relocates the sync problem instead of solving it.

## The actual unresolved question

**Whether unifying is even worth it, given the two repos manage
genuinely different things.** `ansible-proxmox` provisions VM/LXC shells
and manages the Proxmox host itself (`vms.yml`, `lxcs.yml`, CIS
hardening, backups). `ansible-stationctl` picks up after a fresh Debian
install and only ever touches two specific workstations. They don't
share a host list today beyond the fact that `remote-workstation` in
stationctl *is* `vm-debian-workstation-01` in proxmox's `vms.yml` — one
overlapping host out of ~19 total across both repos.

Given that little overlap, a full merged inventory may be solving a
problem that doesn't really exist — the IP-drift risk is real, but a
**per-repo** dynamic Tailscale inventory (no sharing needed, just the
same *pattern* copy-pasted into each repo, same as the CI test suite was)
might get 100% of the benefit without the cross-repo coupling a "linked
inventory" implies.

## Next step, when picked up

Don't build a shared inventory repo/submodule by default. Start by
prototyping a single Tailscale dynamic-inventory script in
`ansible-stationctl` alone (smaller blast radius, only 2 hosts), confirm
it actually simplifies day-to-day use over `check_ip_drift.py`, and only
then decide whether `ansible-proxmox` gets its own copy of the same
script or whether the overlap (just `remote-workstation` /
`vm-debian-workstation-01`) turns out to matter enough to justify real
sharing.
