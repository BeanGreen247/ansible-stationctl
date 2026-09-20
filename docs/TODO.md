# Remaining work

Snapshot as of 2026-09-18 (updated same day — local-workstation is back on
and the full non-bootstrap playbook set has now run against it end to end).

---

## 2026-09-20: VS Code hardening + editor-to-editor SSH (not Ansible-tracked)

Settings Sync was found to be signed into the same GitHub account on both
hosts but had still let `chat.disableAIFeatures` drift out of sync between
them — root-caused and fixed, plus went further since drift like this can
recur:

- Added `/etc/vscode/policy.json` (Ansible-managed, `setup-dev-tools.yml` +
  `vscode_ai_extension_blocklist` in `group_vars/workstations.yml`) —
  machine-level, overrides Settings Sync entirely for AI/telemetry.
- Both hosts' `settings.json` (Settings-Sync-owned, NOT Ansible-managed —
  edited live, propagates on its own): added `git.addAICoAuthor: off`,
  `chat.autopilot.enabled: false`, `workbench.settings.enableNaturalLanguageSearch: false`,
  `extensions.experimental.affinity` (isolates redhat.ansible/vscode-yaml
  into their own extension host), `extensions.experimental.deferredStartupFinishedActivation: true`,
  `python.experiments.enabled: false`.
- **Uninstalled `ms-python.vscode-pylance` from both hosts** — dead weight,
  116MB, `python.languageServer` was already set to `"Jedi"` so Pylance
  never actually activated its language server; also dropped the inert
  `python.analysis.*` settings that only apply to Pylance.
- Removed leftover Pulsar artifacts on local-workstation only
  (`~/.local/bin/pulsar`, `~/.local/share/applications/pulsar.desktop`) —
  pre-Ansible manual leftovers that only became visible once mate-menu
  started scanning `~/.local/share/applications`.
- **New: bean<->kenny personal SSH trust** for editing each machine as
  yourself instead of through the ansibleuser automation account —
  `~/.ssh/id_ed25519_remote-workstation` (bean) / `~/.ssh/id_ed25519_local-workstation`
  (kenny), cross-authorized, plus `Host remote-workstation`/`local-workstation`
  aliases with ControlMaster/ControlPersist in both `~/.ssh/config`s. Lives
  in each user's home dir, not this repo — won't survive a full reprovision
  of either host unless folded into dotfile-sync or a new playbook task.

---

## local-workstation (laptop, bean) — done this session

- **Full playbook run completed and verified** (`setup-base-debian.yml`
  through `setup-security-hardening.yml`, skipping `setup-ansibleuser.yml`
  since the host was already bootstrapped): `ok=178, changed=36, failed=0`.
  Verified from a second SSH path per `docs/QUICKSTART.md`'s hard rule.
- **Swap/disk policy item below is resolved** — confirmed via
  `lsblk -f`/`/proc/swaps` on the real hardware: `nvme0n1` only has the EFI
  partition (p1) and root (p2), no legacy swap partition ever existed here,
  so the swap-reclaim task in `setup-performance-tuning.yml` correctly took
  its "SKIP: no trailing partitions after root" no-op path. Root fs and
  swapfile+zram are both intact and correctly configured. Nothing further
  to do here.
- **Bluetooth support added, bare-metal only** — new
  `workstation_bluetooth_packages` (`group_vars/workstations.yml`, keyed by
  `machine_role` same as GPU/sound) installs `bluez`, `bluez-firmware`,
  `pulseaudio-module-bluetooth`, `blueman` and enables `bluetooth.service`
  on real hardware; empty for VMs. Applied and verified on local-workstation
  (`bluetooth.service` active/enabled, `bean` in the `bluetooth` group).
- **VNC-restart safety gate added** to `setup-remote-access.yml` — before
  restarting the VNC server, it now checks for an established client
  connection on the VNC port and pauses to ask for explicit confirmation
  (skips the restart, config still applies next natural restart) rather
  than silently dropping an active session. Verified on local-workstation
  with no active session (silent no-op, as expected).

## local-workstation — fixed 2026-09-20

- **`tigervncserver@:1.service` fixed** — root cause: the packaged
  `tigervncsession-start` (used by `tigervncserver@.service`) reads the VNC
  password straight from `~/.config/tigervnc/passwd` (XDG) and never
  migrates it from the legacy `~/.vnc/passwd` path itself — only the
  legacy `vncserver` Perl wrapper does that migration. local-workstation's
  password (set 2026-09-19) only ever existed at the legacy path, so
  Xtigervnc found no password file, fell back to an interactive prompt on
  the service's non-interactive TTY, and died
  (`getpassword error: Inappropriate ioctl for device`, confirmed via
  `~/.local/state/tigervnc/PeakPulse:1.log`). remote-workstation happened
  to already have both copies (from an earlier manual `vncserver` run) so
  it never hit this. `setup-remote-access.yml` now mirrors the password to
  both paths unconditionally; service verified `active` and the playbook
  re-runs clean (`changed=0`) on both hosts.

- **`ayatana-indicator-application.service` (local-workstation) loses the
  StatusNotifierWatcher race to `mate-indicator-applet-complete` — found
  2026-09-19 via the idempotency wrapper on `setup-dev-applets.yml`.**
  Live evidence: `mate-indicator-applet-complete` (part of the real
  logged-in MATE session, `dbus-send --session ListNames` confirms it)
  already owns `org.kde.StatusNotifierWatcher`; the ansible-started
  `ayatana-indicator-application.service` then fails to also claim that
  name (`journalctl --user`: "Unable to get watcher name... Name Lost")
  and lands `Active: inactive (dead)` every time, so the task's `systemd:
  state=started` reports `changed=true` on every single run — permanent,
  not transient. **Not yet investigated further or fixed** — the task's
  own comment in `setup-dev-applets.yml` (around "Look up end-user's
  uid") justifies this service as needed for the Qt6 tray apps
  (dotfile-sync-tray, infra-connections) to have a watcher to register
  with, but live evidence now shows `mate-indicator-applet-complete`
  already fills that role and the tray apps are already working through
  it — so `ayatana-indicator-application.service` may simply be redundant
  now rather than needed. Didn't touch this mid-sweep since it's a live,
  currently-working tray setup people rely on daily; left the
  idempotency lock ack'd, not silenced. Next step: check whether
  disabling this service (instead of enabling it) still leaves the tray
  apps working, on a real session, before changing the playbook.

## remote-workstation (VM, kenny) — carried over, still open

- **VS Code Settings Sync isn't actually turned on yet** — manual,
  interactive GitHub sign-in step in the VS Code UI.
- **Todo-Tree ripgrep fix not yet confirmed working** — open a workspace
  with a `TODO`/`FIXME` comment and check the panel populates without the
  "Failed to find vscode-ripgrep" error.
- **Keyring-unlock dialog fix confirmed via live D-Bus testing and a VNC
  session restart, not yet across an actual `reboot`** on this host.
  Worth confirming next time it reboots for any reason.

## local-workstation — carried over, still open

- **ZeroTier** — user is installing/configuring this themselves on real
  hardware only. `infra-connections.py` already handles it gracefully
  either way.
- **Confirm the keyring-unlock proactive-autostart fix across a real
  reboot** — same caveat as remote-workstation above.
- **VS Code Settings Sync + Todo-Tree** — same as remote-workstation.
- **Physical GUI login sanity check** — lightdm is enabled/unmasked;
  worth confirming a cold boot (not just warm-restart) still reaches a
  clean MATE desktop.

## General / repo-level

- **2026-09-19: CI added** — `.github/workflows/ci.yml` runs
  `scripts/test_playbooks.sh` (yamllint + `ansible-playbook --syntax-check`
  for every top-level playbook + `ansible-lint --profile basic`) on every
  push/PR. This catches the class of bug that would blow up immediately on
  execution (bad YAML, undefined vars, broken Jinja, deprecated module
  args) — it can't catch "logically wrong for this specific hardware"
  bugs, since these playbooks depend on real host facts (`machine_role`,
  disk layout, etc.) that don't exist in a lint-only pass. Real execution
  correctness still comes from `scripts/run_playbook.py`'s idempotency
  postflight against the two real hosts. `ansible-lint` is pinned to the
  `basic` profile (not `production`) on purpose — the stricter profile
  surfaced ~40 pre-existing style findings (no-handler, no-changed-when,
  document-start, etc.) across the repo that are real but out of scope for
  this pass; tightening the profile is a deliberate future cleanup, not
  bundled in here.
  - **Follow-up, same as below**: port this same lint/syntax-check CI setup
    to `ansible-proxmox` too — noted in that repo's memory file, not yet
    built there.
- **2026-09-19: added `scripts/run_playbook.py` + `scripts/check_ip_drift.py`**
  — IP-drift preflight (inventory vs. live Tailscale lookup, cross-checked
  against ARP when a MAC is known) and an idempotency postflight (rerun
  with `--check --diff`, halt if changed>0) wrapped around every routine
  playbook run. See `docs/QUICKSTART.md`'s new section for usage. Verified
  end-to-end against a synthetic drift/non-idempotent case; **not yet
  exercised on a real drift event** (both hosts currently resolve clean).
  Follow-ups:
  - Add `mac_address` to `host_vars/*/main.yml` for both hosts (need to
    pull each host's real MAC via `ip link`/`cat /sys/class/net/*/address`
    over SSH) to enable the ARP cross-check — currently skipped with a
    note on every run.
  - Port the same pattern to `ansible-proxmox` — see that repo's memory
    note; the navidrome IP-drift incident that prompted this lives there,
    not here.
  - **Future feature, explicitly undecided**: whether to replace this
    drift-detection-on-a-static-file approach with a live Tailscale-based
    dynamic inventory in both repos instead. See
    `docs/INVENTORY-CONSOLIDATION.md` — proposal only, not started.
- **`docs/README.md` reconciliation** — lower priority, purely
  cosmetic/accuracy; `QUICKSTART.md` is the accurate day-to-day reference
  in the meantime. The README's "Pending Tasks" swap/disk entry for
  local-workstation is now stale (see "done this session" above) — update
  or remove it in that pass.

---

## Already done (for reference — not remaining work)

Everything from earlier sessions: Tailscale IPs in inventory,
disk-layout/EFI-partition fix, GPU/sound drivers (bare-metal only),
lightdm gating by `machine_role`, Papirus-Dark theme, the full dev-applets
port (apt-key-refresh/dotfile-sync/trays) with the Qt6 StatusNotifierWatcher
fix, the bean-vs-kenny account split with the `update_password: on_create`
safety fix, the fstab UUID-matching fix, the full build-dependency audit
(fleetwm/.NET/Armbian Build Framework) applied fleet-wide, VS Code
installed with Copilot/chat disabled, Brave/VS Code `password-store=basic`,
Pulsar/Zed removed from both machines, the conservative Lynis-hardening
subset (`setup-security-hardening.yml`), plus everything under "done this
session" above.
