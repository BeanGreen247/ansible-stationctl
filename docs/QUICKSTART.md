# Quickstart — making and applying changes

Task-oriented companion to [README.md](README.md) (that one's the full
reference; this one's "I want to do X, what do I edit and run"). See README's
[Requirements](README.md#requirements) section before your very first run.

---

## The one gotcha that bites every time

**Never run bare `site.yml` once `ansibleuser` already exists on a host.**
`site.yml`'s first play (`setup-ansibleuser.yml`) hardcodes `ansible_user:
root` to bootstrap a brand-new machine. On an already-bootstrapped host, root
has no key trust, every task in that play fails, and the repeated failed-auth
attempts can trip SSH's per-source connection penalty and break the *next*
play's connections too.

Once a host is bootstrapped (i.e. always, after the very first run), run the
remaining playbooks **individually**, in the same order `site.yml` uses:

```bash
ansible-playbook setup-base-debian.yml \
                  setup-mate-desktop.yml \
                  setup-dev-tools.yml \
                  setup-remote-access.yml \
                  setup-performance-tuning.yml \
                  setup-kernel-perf-tuning.yml \
                  setup-dev-applets.yml \
                  --limit workstations
```

Only use `ansible-playbook site.yml -u root --ask-pass --limit <host>` (or
`--limit workstations`) for a genuinely fresh machine that has no
`ansibleuser` yet — see [Onboarding a brand-new workstation](#onboarding-a-brand-new-workstation) below.

---

## "I want to change X" → edit here → run this

| I want to... | Edit | Then run |
|---|---|---|
| Add a package for **every** workstation | `group_vars/workstations.yml` → `dev_packages` / `sysadmin_cli_packages` / `debian_base_packages` | `ansible-playbook setup-dev-tools.yml --limit workstations` (or `setup-base-debian.yml` for `debian_base_packages`) |
| Add GPU or sound driver packages | `group_vars/workstations.yml` → `workstation_gpu_packages` / `workstation_sound_packages`, keyed by `machine_role` (`bare-metal`/`vm`) | `ansible-playbook setup-base-debian.yml --limit workstations` |
| Change the MATE theme / icon set | `group_vars/workstations.yml` → `mate_gtk_theme` / `mate_marco_theme` / `mate_icon_theme` (+ matching package in `mate_packages` if it's a new theme/icon set) | `ansible-playbook setup-mate-desktop.yml --limit workstations` |
| Change what `dotfile-sync` pulls | `group_vars/workstations.yml` → `dotfile_sync_files` | `ansible-playbook setup-dev-applets.yml --limit workstations` |
| Add/remove a third-party apt repo key to watch | `files/dev-applets/apt-key-refresh.sh` → `KEY_TABLE` array | `ansible-playbook setup-dev-applets.yml --limit workstations` (safe to skip re-running — the systemd timer picks it up within a week, or run `sudo apt-key-refresh` on the host now) |
| Change disk-specific settings for one host (swap size, drive type) | `host_vars/<host>/main.yml` → `workstation_drives` / `workstation_swapfile_gb` / `workstation_swappiness` etc | `ansible-playbook setup-performance-tuning.yml setup-kernel-perf-tuning.yml --limit <host>` |
| Change which real login account a host manages | `host_vars/<host>/main.yml` → `vm_enduser_name` (only needed when it differs from `group_vars/all/main.yml`'s default — see [local-workstation's override](../host_vars/local-workstation/main.yml) for the pattern) | `ansible-playbook setup-base-debian.yml setup-remote-access.yml setup-dev-applets.yml --limit <host>` |
| Change GRUB / kernel cmdline tuning | `setup-kernel-perf-tuning.yml` (task list) or `group_vars/workstations.yml` for host-agnostic knobs | `ansible-playbook setup-kernel-perf-tuning.yml --limit workstations`, then **reboot** (cmdline changes need it) |
| Enable `mitigations=off` on one host | `host_vars/<host>/main.yml` → `workstation_kernel_mitigations_off: true` | `ansible-playbook setup-kernel-perf-tuning.yml --limit <host>`, then reboot |
| Add a Python pip tool used by home-dir scripts | `group_vars/workstations.yml` → `workstation_pip_packages` | `ansible-playbook setup-dev-tools.yml --limit workstations` |

After **any** change to `group_vars/workstations.yml`, the safe blanket move
is just re-running the single playbook that owns the list you touched — every
playbook here is idempotent, so it's always safe to re-run.

---

## Onboarding a brand-new workstation

1. Fresh minimal Debian 13 (trixie) install on the target.
2. Add its IP to `inventory/hosts.ini` (new `[hostname]` group + add it to
   `[workstations:children]`).
3. Create `host_vars/<hostname>/main.yml` — at minimum set `machine_role`
   (`bare-metal` or `vm`) and `workstation_drives` (device name → `ssd`/`hdd`,
   verify with `lsblk -d -o NAME,ROTA,TYPE` — this is **mandatory**, the
   kernel-tuning play asserts it's set). If the host's real login account
   differs from `group_vars/all/main.yml`'s `vm_enduser_name`, override it
   here too (see local-workstation's `main.yml` for why/how).
4. Bootstrap the ansible user:
   ```bash
   ansible-playbook setup-ansibleuser.yml -u root --ask-pass --limit <hostname>
   ```
   (If root has no password auth either — some fresh installs only have a
   named sudo user, no root login at all — bootstrap as that user instead:
   `-e ansible_user=<thatuser> -e ansible_become_pass=<theirpassword>` via a
   vars file, not on the command line where it'd land in shell history.)
5. Full bring-up:
   ```bash
   ansible-playbook site.yml --limit <hostname>
   ```
6. **Reboot** — GRUB cmdline flags from `setup-kernel-perf-tuning.yml` need
   it. Verify after: `cat /proc/cmdline` should show `nowatchdog noirqdebug
   threadirqs preempt=full transparent_hugepage=madvise audit=0 loglevel=3`
   (plus `mitigations=off` if you opted into that).
7. One-time manual steps (deliberately not automated — see README's
   [Known Limitations](README.md#known-limitations)):
   - SSH in and run `vncpasswd` as the end-user to set the VNC password.
   - `tailscale up` on the machine to authenticate.
   - The **first time** something needs the login keyring (VS Code Settings
     Sync sign-in, Brave saving a password, etc.), expect one real
     "Unlock/Create keyring" GUI dialog in that session — answer it with a
     **blank password**. gnome-keyring has no working non-interactive path
     for this (tested extensively — see `setup-dev-applets.yml`'s keyring
     script comment); after this one click, the collection stays unlocked
     for the rest of that boot/session for every app.
8. If the host has real login credentials for a physical console (a laptop,
   not a headless VM), lightdm is already enabled/started automatically —
   log in physically, and the two tray applets (`dotfile-sync-tray`,
   `infra-connections`) should autostart. If the panel's indicator slot
   looks empty/broken right after this first run, that's expected — the
   session that was live *before* `mate-indicator-applet`/`python3-pyqt6`
   got installed never re-read the new panel layout or autostart list. A
   normal logout/login or reboot clears it; no playbook fix needed.

---

## Quick post-apply verification

```bash
# Swap/cache policy and most kernel tuning apply immediately, no reboot needed:
free -h; cat /proc/swaps
sysctl vm.swappiness vm.vfs_cache_pressure vm.min_free_kbytes
cat /sys/kernel/mm/transparent_hugepage/enabled   # expect [madvise]
sysctl net.ipv4.tcp_congestion_control            # expect bbr
systemctl is-active earlyoom irqbalance           # expect active
systemctl is-active systemd-oomd                  # expect inactive

# GRUB cmdline flags — only correct AFTER a reboot:
cat /proc/cmdline

# Dev-applets:
sudo apt-key-refresh --check          # dry-run key check
sudo -u <user> dotfile-sync --status  # last sync info
systemctl --user status dotfile-sync.timer   # (as that user, with XDG_RUNTIME_DIR set)
```

If GRUB flags are missing after a reboot, `grub.cfg` was never regenerated —
re-run `ansible-playbook setup-kernel-perf-tuning.yml --limit <host> --tags
kperf,grub` (it actively checks `grub.cfg` content, not just whether the
drop-in source changed) and reboot again.
