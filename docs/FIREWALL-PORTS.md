# Firewall ports — how to add/change one

`security-harden.yml`'s CIS 4 section puts every host in `workstations`
behind UFW with a default-deny-incoming policy. SSH (22/tcp) is opened
automatically for every host. **Everything else a host needs must be
declared per-host** — nothing else is open by default.

This matters more here than it might look: neither `remote-workstation`
nor `local-workstation` ran any firewall at all before `security-harden.yml`
was unified with ansible-proxmox's copy of the same file (see
`docs/INVENTORY-CONSOLIDATION.md` for why these two repos stay separate
otherwise). The very first run of this playbook on either workstation is
the first time a default-deny firewall has ever been in front of them.

This file only covers ansible-stationctl's own hosts (`workstations`
group: `remote-workstation` and `local-workstation`). ansible-proxmox has
its own copy of this same doc for its `lxcs`/`vms` fleet — see that
repo's `docs/FIREWALL-PORTS.md`. Two repos, two fleets — don't declare
one repo's ports in the other's host_vars.

## How to add a port

1. Open (or create) `host_vars/<hostname>/main.yml` for the workstation.
2. Add or extend `cis_ufw_extra_ports`:

   ```yaml
   cis_ufw_extra_ports:
   - { port: "5901", proto: "tcp", comment: "TigerVNC remote desktop" }
   ```

   `port` can be a single port or a range (`"40000:40100"`). `proto`
   defaults to `tcp` if omitted — declare `udp` explicitly as a separate
   list entry if the service needs it too.

3. **Do not** add `cis_ufw_extra_ports` in `security-harden.yml`'s own
   `vars:` block — host_vars would be silently overridden. This variable
   only belongs in `host_vars/<host>/main.yml`.

## How to apply it

```bash
ansible-playbook security-harden.yml --limit <hostname> --tags cis4
```

`--tags cis4` re-runs only the firewall section. Drop the tag for a
first-time run on a workstation that hasn't had the full playbook applied
yet.

## How to verify it actually worked

```bash
ansible <hostname> -m shell -a "ufw status verbose" -b
```

Cross-check against what's actually listening:

```bash
ansible <hostname> -m shell -a "ss -tulnp" -b
```

Anything bound to `0.0.0.0`/`[::]` (not `127.0.0.1`/`[::1]`) that isn't
covered by an existing `cis_ufw_extra_ports` entry will go dark the next
time CIS 4 runs on that host — for these two machines, that's most
directly TigerVNC (`setup-remote-access.yml`, port derived from
`vnc_display` in `group_vars/workstations.yml` — `:1` → 5901) and SSH
itself.

`local-workstation` (the bare-metal ThinkPad) is not always reachable to
verify live — it's a personal machine, not an always-on server. When it's
offline, go by parity with `remote-workstation`'s confirmed config (same
`vnc_display` group var, same `setup-remote-access.yml` role) rather than
guessing at something new.

## Current per-host ports (as of the 2026-09-21 audit)

| Host | Ports | Notes |
|---|---|---|
| `remote-workstation` | 5901 tcp | Confirmed live via `ss` — TigerVNC (`Xtigervnc`), nothing else listening beyond SSH/Tailscale |
| `local-workstation` | 5901 tcp | Declared by parity with `remote-workstation`; not independently confirmed (host was offline during the audit) |

If a workstation ever gets a new remote-access method, dev tool that opens
a network port, or web UI, add it here and in the corresponding
`host_vars` entry in the same change.
