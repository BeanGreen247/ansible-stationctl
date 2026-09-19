# ansible-stationctl

> Zero-manual-labor MATE workstation automation — one playbook, identical environment on a Proxmox VM and on bare-metal hardware alike.

> **In a hurry?** See [QUICKSTART.md](QUICKSTART.md) for "I want to change X, what do I edit and run" — this file is the full reference.

---

## Table of Contents

1. [Overview](#overview)
2. [Tested Hardware Platform](#tested-hardware-platform)
3. [Repository Layout](#repository-layout)
4. [Requirements](#requirements)
5. [First-Time Setup](#first-time-setup)
6. [Step-by-Step: Applying This Setup on a New VM or Bare-Metal Machine](#step-by-step-applying-this-setup-on-a-new-vm-or-bare-metal-machine)
7. [Playbook Reference](#playbook-reference)
8. [Configuration Reference](#configuration-reference)
9. [Inventory](#inventory)
10. [Day-to-Day Operations](#day-to-day-operations)
11. [Vault — Managing Secrets](#vault--managing-secrets)
12. [Known Limitations](#known-limitations)
13. [Pending Tasks](#pending-tasks)

---

## Overview

This repo stands up and maintains two workstations from a fresh Debian install onward:

- **remote-workstation** — a VM on the homelab Proxmox host, 2 vCPU / 2–4 GB RAM budget. Replaces the current Guacamole-entry VM once proven; that VM stays untouched until then.
- **local-workstation** — native Debian installed directly on the ThinkPad E490 (no virtualization), same package/config set.

The point: edit `group_vars/workstations.yml` or any playbook once, re-run `site.yml`, and both machines converge to match — no manual per-machine setup, ever.

VM *shell* creation (Proxmox API provisioning) is out of scope here — that's [`ansible-proxmox`](../ansible-proxmox)'s job. This repo picks up from a fresh Debian install, the same handoff point as `ansible-proxmox`'s own `setup-ansibleuser.yml` → `setup-debian-base.yml` chain.

`ansible-proxmox-ssh-gui-tool` is a separate, standalone tool — not part of this repo.

## Tested Hardware Platform

`remote-workstation` is a Proxmox VM (virtual hardware, sized per
`ansible-proxmox`'s `group_vars/all/vms.yml`). `local-workstation` is the
one real bare-metal box this repo is validated against — profile below
gathered live from the host itself (`lshw`, `dmidecode`, `lscpu`,
`lsblk`, `lspci`) on 2026-09-19, not from memory or a spec sheet:

| Component | Detail |
|---|---|
| Model | Lenovo ThinkPad E490 (20N9S19A00) |
| CPU | Intel Core i5-8265U, 4 cores / 8 threads, up to 3.9GHz |
| RAM | 40GB DDR4 (32GB + 8GB SODIMM, mixed 3200/2667 MT/s modules, both running at 2400 MT/s) |
| Storage | Toshiba KXG60ZNV256G NVMe, 256GB (238.5GB usable: ~975MB EFI + 237GB ext4 root) |
| GPU | Intel UHD Graphics 620 (integrated, WhiskeyLake-U GT2) |
| Wired NIC | Realtek RTL8111/8168/8211/8411 Gigabit Ethernet |
| Wi-Fi / Bluetooth | Intel Wi-Fi 6 AX200 (also provides Bluetooth) |
| OS | Debian 13 (trixie), kernel 6.12.107+deb13-amd64 |

Why this matters: `machine_role: bare-metal` branches in
`setup-kernel-perf-tuning.yml`, `setup-performance-tuning.yml`, and the
Bluetooth/GPU/sound package selection in `group_vars/workstations.yml`
run on this real hardware, not a VM approximating it — GRUB cmdline
changes, the NVMe drive-type map, and the swap/zram tier this box lands
on (see `host_vars/local-workstation/main.yml`) are all validated against
actual firmware and actual silicon. If you're adapting this repo to
different real hardware, treat every bare-metal-only task as "tested on
one specific laptop," not "tested on bare metal in general."

## Repository Layout

```
ansible-stationctl/
├── ansible.cfg                    # vault path, collections path, forks, ssh args
├── collections/requirements.yml   # community.general, ansible.posix
├── inventory/hosts.ini            # [remote-workstation], [local-workstation], [workstations:children]
├── group_vars/
│   ├── all/
│   │   ├── main.yml               # gitignored — real secrets, copy from example_of_main.yml
│   │   └── example_of_main.yml    # committed template
│   └── workstations.yml           # single source of truth: packages, theme, resource budget
├── host_vars/
│   ├── remote-workstation/main.yml
│   └── local-workstation/main.yml
├── templates/                     # dconf keyfiles, systemd unit templates
├── setup-ansibleuser.yml          # bootstrap: create ansibleuser, sudo, SSH key
├── setup-base-debian.yml          # apt update/upgrade, base packages, SSH hardening, end-user account
├── setup-mate-desktop.yml         # minimal MATE, single bottom panel, dark theme, compositing off
├── setup-dev-tools.yml            # git, build-essential, docker, python3, nodejs, gh
├── setup-remote-access.yml        # TigerVNC + Tailscale
├── setup-performance-tuning.yml   # zram, swappiness, disable unneeded services
├── setup-kernel-perf-tuning.yml   # CPU governor, GRUB cmdline, sysctl/IO/network tuning, earlyoom
├── site.yml                       # runs everything above, in order
└── docs/README.md                 # this file
```

No `roles/` directory — flat, tagged playbooks, same convention as `ansible-proxmox`.

## Requirements

- Ansible core ≥ 2.15 on the controller
- `ansible-lint` (optional but recommended — already in `~/.local/bin` if you followed the same setup as `ansible-proxmox`)
- Collections: `ansible-galaxy collection install -r collections/requirements.yml`
- `~/.vault_pass.txt` (shared with `ansible-proxmox` — same file, same passphrase)
- SSH key at `~/.ssh/id_rsa` with access to both target machines as `root` (for the bootstrap run) and later as `ansibleuser`

## First-Time Setup

1. Install a fresh minimal Debian 13 (trixie) on both targets (VM shell via `ansible-proxmox`, laptop via a manual/netinst Debian install — no preseed automation for bare metal in this repo yet).
2. Add each host's IP to `inventory/hosts.ini` under `[remote-workstation]` / `[local-workstation]`.
3. `cp group_vars/all/example_of_main.yml group_vars/all/main.yml` and fill in real values (never commit this file — it's gitignored).
4. Bootstrap: `ansible-playbook setup-ansibleuser.yml -u root --limit workstations`
5. Full bring-up: `ansible-playbook site.yml`
6. One-time manual step (secret, not automated — see [Known Limitations](#known-limitations)): SSH in and run `vncpasswd` as the end-user to set the VNC password.
7. One-time manual step: `tailscale up` on each machine to authenticate.

## Step-by-Step: Applying This Setup on a New VM or Bare-Metal Machine

Same procedure for `remote-workstation` (Proxmox VM) and `local-workstation` (bare-metal ThinkPad E490) — the differences are called out inline. This is the sequence that's actually been run and verified end-to-end on `remote-workstation`.

1. **Install fresh minimal Debian 13 (trixie).**
   - VM: via `ansible-proxmox`'s `auto-install-debian.yml` (preseed, `no_swap=true` — no swap partition is created, this repo's `setup-performance-tuning.yml` owns swap entirely as zram + `/swapfile`).
   - Bare metal: manual/netinst Debian install. **Before running `--tags perf` the first time**, check the real disk layout (`lsblk -f`, `sfdisk -d /dev/sdX`) — the installer may have created a real swap partition. Decide whether to keep it (`workstation_remove_legacy_swap_partitions: false` in that host's `host_vars/*/main.yml`) or let this repo retire it — see [Pending Tasks](#pending-tasks) for the full bare-metal caution list. Take a disk backup/snapshot first; bare metal has no Proxmox snapshot safety net.
2. **Add the host to `inventory/hosts.ini`** under `[remote-workstation]` or `[local-workstation]` with its real IP (or `ansible_connection=local` if running directly on that machine).
3. **Set up secrets**: `cp group_vars/all/example_of_main.yml group_vars/all/main.yml`, fill in real values, never commit it.
4. **Bootstrap the ansible user**: `ansible-playbook setup-ansibleuser.yml -u root --limit <hostname>`
5. **Dry-run first** (especially on bare metal, since `setup-performance-tuning.yml` can grow the root filesystem into reclaimed swap-partition space):
   ```bash
   ansible-playbook site.yml --limit <hostname> --check --diff
   ```
6. **Full bring-up for real**: `ansible-playbook site.yml --limit <hostname>`. This chains every layer including `setup-kernel-perf-tuning.yml` (CPU governor, GRUB cmdline, sysctls, BBR, IO scheduler, earlyoom) at the end.
7. **Verify what applied *without* a reboot** — swap/cache policy and most kernel tuning take effect immediately:
   ```bash
   free -h; cat /proc/swaps
   sysctl vm.swappiness vm.vfs_cache_pressure vm.min_free_kbytes
   cat /sys/kernel/mm/transparent_hugepage/enabled   # expect [madvise]
   cat /sys/block/sdX/queue/scheduler                # expect [bfq] on rotational, [none] on SSD/NVMe
   sysctl net.ipv4.tcp_congestion_control             # expect bbr
   systemctl is-active earlyoom irqbalance preload    # expect active
   systemctl is-active systemd-oomd                   # expect inactive (disabled in favor of earlyoom)
   ```
8. **Reboot** — required for the GRUB cmdline flags (`nowatchdog noirqdebug threadirqs preempt=full transparent_hugepage=madvise audit=0 loglevel=3`, plus `mitigations=off` if that opt-in is set) to actually take effect. Do this whenever's convenient; nothing above depends on it.
9. **Verify the reboot picked up the cmdline flags**:
   ```bash
   cat /proc/cmdline
   ```
   If the flags are missing after a reboot, `grub.cfg` was never regenerated — re-run `ansible-playbook setup-kernel-perf-tuning.yml --limit <hostname> --tags kperf,grub` (idempotent; it now actively checks `grub.cfg` content, not just whether the drop-in source file changed) and reboot again.
10. **One-time manual steps** (secrets/interactive auth, intentionally not automated): SSH in and run `vncpasswd` as the end-user, then `tailscale up` on the machine to authenticate.

## Playbook Reference

| Playbook | Purpose | Tags |
|---|---|---|
| `setup-ansibleuser.yml` | Bootstrap `ansibleuser` with sudo + SSH key | — |
| `setup-base-debian.yml` | APT update/upgrade, base packages, SSH key-only, end-user account | `base` |
| `setup-mate-desktop.yml` | Minimal MATE, single bottom panel, dark theme, compositing off | `mate` |
| `setup-dev-tools.yml` | git, build-essential, Docker, Python, Node.js, GitHub CLI | `dev` |
| `setup-remote-access.yml` | TigerVNC (MATE session) + Tailscale | `remote-access` |
| `setup-performance-tuning.yml` | zram, swappiness, disables unneeded services | `perf` |
| `setup-kernel-perf-tuning.yml` | CPU governor, GRUB cmdline flags (reboot required), extra vm/kernel/net sysctls, BBR, IO scheduler, irqbalance/preload, earlyoom | `kperf` |
| `site.yml` | Runs all of the above, in dependency order | any of the above |

Run a single layer with `--tags`, a single machine with `--limit`:

```bash
ansible-playbook site.yml --limit local-workstation
ansible-playbook site.yml --tags mate
```

## Configuration Reference

Everything tunable lives in `group_vars/workstations.yml`:

- vCPU/RAM sizing is NOT in this repo — it lives in `ansible-proxmox/group_vars/all/vms.yml` (the `vm-debian-workstation-01` entry). Change it there and re-run `create-vm-from-iso-proxmox.yml`, not here.
- `workstation_zram_percent` / `workstation_swappiness` — performance tuning knobs
- `debian_base_packages`, `mate_packages`, `dev_packages`, `remote_access_packages` — the package lists that define "the environment"
- `mate_gtk_theme` / `mate_marco_theme` / `mate_icon_theme` — currently `Arc-Dark` as a placeholder for the "submarine" look; swap freely
- `vnc_display` / `vnc_geometry` / `vnc_depth` — VNC session settings
- `workstation_kernel_mitigations_off` (host_vars, default unset/false) — appends `mitigations=off` to the GRUB cmdline in `setup-kernel-perf-tuning.yml`. Real perf gain on older CPUs, real security cost (disables Spectre/Meltdown/etc CPU-vulnerability mitigations) — opt in per host deliberately, not a repo-wide default.

## Inventory

`[workstations:children]` aggregates `[remote-workstation]` + `[local-workstation]`. Both are managed identically as real SSH hosts. `local-workstation` may set `ansible_connection=local` in its inventory line if you run the playbook directly on the laptop instead of over SSH.

## Day-to-Day Operations

- **Add a package everywhere**: edit the relevant list in `group_vars/workstations.yml`, re-run `ansible-playbook site.yml --tags dev` (or `mate`, `perf`, etc.)
- **Change the theme**: edit `mate_gtk_theme`/`mate_marco_theme` in `group_vars/workstations.yml`, re-run `--tags mate`
- **Re-apply everything from scratch on one machine**: `ansible-playbook site.yml --limit remote-workstation`
- **Reapply a config change**: every playbook here is idempotent, so after editing `group_vars/workstations.yml`, a `host_vars/*` file, or a playbook itself, just re-run `site.yml` (or the single relevant `--tags` layer) against the affected host(s) — no need to tear anything down first:
  ```bash
  ansible-playbook site.yml --limit remote-workstation --tags mate
  ```
  If `ansibleuser` already exists on the target, skip untagged/unlimited `site.yml` runs and invoke the specific playbook(s) directly instead (e.g. `ansible-playbook setup-mate-desktop.yml --limit remote-workstation`) — running `site.yml` from the top re-attempts `setup-ansibleuser.yml`'s root bootstrap, and the failed root auth can trip SSH's per-source connection-penalty and break the next play's connection for a short cooldown.

## Vault — Managing Secrets

Same pattern as `ansible-proxmox`: `~/.vault_pass.txt` (outside any repo, `chmod 600`), referenced by `ansible.cfg`'s `vault_password_file`. `group_vars/all/main.yml` and `host_vars/*/vault.yml` are gitignored — **secrets are never committed**, moved to each workstation manually or via `scp` only.

## Known Limitations

- **MATE is X11-only** in Debian 13/trixie — no stable Wayland session (unlike the KDE Plasma setup on this same laptop's existing install). Remote access uses TigerVNC (X11), not a Wayland remote-desktop protocol. Deliberate trade-off for the lighter resource footprint.
- **VNC password and Tailscale auth are manual, one-time steps** — they're secrets/interactive-auth flows that intentionally stay out of the playbooks and the repo.
- **Panel layout is a best-effort dconf default** — written from known MATE dconf schema paths but not yet visually verified on a live session. Use MATE Panel's own reset/tweak if it's not pixel-perfect on first boot, then encode the fix back into the playbook.

## Pending Tasks

See [`docs/TODO.md`](TODO.md) for the current list — it's updated per-session and is the accurate source now. (The `local-workstation` swap/disk policy decision that used to live here is resolved: confirmed 2026-09-18 via `lsblk -f`/`/proc/swaps` on the real ThinkPad disk that no legacy swap partition ever existed, so `setup-performance-tuning.yml`'s reclaim task correctly took its no-op path; root fs and swapfile+zram are intact.)

See [`docs/INVENTORY-CONSOLIDATION.md`](INVENTORY-CONSOLIDATION.md) for an open (undecided) proposal to replace this repo's and `ansible-proxmox`'s static inventory files with a live Tailscale-based lookup.
