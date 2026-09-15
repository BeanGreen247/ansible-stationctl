# ansible-stationctl

> Zero-manual-labor MATE workstation automation — one playbook, identical environment on a Proxmox VM and on bare-metal hardware alike.

---

## Table of Contents

1. [Overview](#overview)
2. [Repository Layout](#repository-layout)
3. [Requirements](#requirements)
4. [First-Time Setup](#first-time-setup)
5. [Playbook Reference](#playbook-reference)
6. [Configuration Reference](#configuration-reference)
7. [Inventory](#inventory)
8. [Day-to-Day Operations](#day-to-day-operations)
9. [Vault — Managing Secrets](#vault--managing-secrets)
10. [Known Limitations](#known-limitations)

---

## Overview

This repo stands up and maintains two workstations from a fresh Debian install onward:

- **remote-workstation** — a VM on the homelab Proxmox host, 2 vCPU / 2–4 GB RAM budget. Replaces the current Guacamole-entry VM once proven; that VM stays untouched until then.
- **local-workstation** — native Debian installed directly on the ThinkPad E490 (no virtualization), same package/config set.

The point: edit `group_vars/workstations.yml` or any playbook once, re-run `site.yml`, and both machines converge to match — no manual per-machine setup, ever.

VM *shell* creation (Proxmox API provisioning) is out of scope here — that's [`ansible-proxmox`](../ansible-proxmox)'s job. This repo picks up from a fresh Debian install, the same handoff point as `ansible-proxmox`'s own `setup-ansibleuser.yml` → `setup-debian-base.yml` chain.

`ansible-proxmox-ssh-gui-tool` is a separate, standalone tool — not part of this repo.

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

## Playbook Reference

| Playbook | Purpose | Tags |
|---|---|---|
| `setup-ansibleuser.yml` | Bootstrap `ansibleuser` with sudo + SSH key | — |
| `setup-base-debian.yml` | APT update/upgrade, base packages, SSH key-only, end-user account | `base` |
| `setup-mate-desktop.yml` | Minimal MATE, single bottom panel, dark theme, compositing off | `mate` |
| `setup-dev-tools.yml` | git, build-essential, Docker, Python, Node.js, GitHub CLI | `dev` |
| `setup-remote-access.yml` | TigerVNC (MATE session) + Tailscale | `remote-access` |
| `setup-performance-tuning.yml` | zram, swappiness, disables unneeded services | `perf` |
| `site.yml` | Runs all of the above, in dependency order | any of the above |

Run a single layer with `--tags`, a single machine with `--limit`:

```bash
ansible-playbook site.yml --limit local-workstation
ansible-playbook site.yml --tags mate
```

## Configuration Reference

Everything tunable lives in `group_vars/workstations.yml`:

- `workstation_vcpu_budget` / `workstation_ram_mb_budget` — informational, documents the target envelope
- `workstation_zram_percent` / `workstation_swappiness` — performance tuning knobs
- `debian_base_packages`, `mate_packages`, `dev_packages`, `remote_access_packages` — the package lists that define "the environment"
- `mate_gtk_theme` / `mate_marco_theme` / `mate_icon_theme` — currently `Arc-Dark` as a placeholder for the "submarine" look; swap freely
- `vnc_display` / `vnc_geometry` / `vnc_depth` — VNC session settings

## Inventory

`[workstations:children]` aggregates `[remote-workstation]` + `[local-workstation]`. Both are managed identically as real SSH hosts. `local-workstation` may set `ansible_connection=local` in its inventory line if you run the playbook directly on the laptop instead of over SSH.

## Day-to-Day Operations

- **Add a package everywhere**: edit the relevant list in `group_vars/workstations.yml`, re-run `ansible-playbook site.yml --tags dev` (or `mate`, `perf`, etc.)
- **Change the theme**: edit `mate_gtk_theme`/`mate_marco_theme` in `group_vars/workstations.yml`, re-run `--tags mate`
- **Re-apply everything from scratch on one machine**: `ansible-playbook site.yml --limit remote-workstation`

## Vault — Managing Secrets

Same pattern as `ansible-proxmox`: `~/.vault_pass.txt` (outside any repo, `chmod 600`), referenced by `ansible.cfg`'s `vault_password_file`. `group_vars/all/main.yml` and `host_vars/*/vault.yml` are gitignored — **secrets are never committed**, moved to each workstation manually or via `scp` only.

## Known Limitations

- **MATE is X11-only** in Debian 13/trixie — no stable Wayland session (unlike the KDE Plasma setup on this same laptop's existing install). Remote access uses TigerVNC (X11), not a Wayland remote-desktop protocol. Deliberate trade-off for the lighter resource footprint.
- **VNC password and Tailscale auth are manual, one-time steps** — they're secrets/interactive-auth flows that intentionally stay out of the playbooks and the repo.
- **Panel layout is a best-effort dconf default** — written from known MATE dconf schema paths but not yet visually verified on a live session. Use MATE Panel's own reset/tweak if it's not pixel-perfect on first boot, then encode the fix back into the playbook.
