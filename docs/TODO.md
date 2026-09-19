# Remaining work

Snapshot as of 2026-09-18 (updated same day — local-workstation is back on
and the full non-bootstrap playbook set has now run against it end to end).

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

## local-workstation — found, not yet fixed

- **`tigervncserver@:1.service` fails to start** (`exited with status=1`,
  no `:1`-display log written) — confirmed via journal this predates
  today's session (same failure recurring since at least 2026-09-17), so
  it's **not** something today's playbook run caused. SSH and physical
  console login are unaffected; only VNC remote desktop to this host is
  down. Diagnosing further needs starting `tigervncserver :1` interactively
  as `bean` (blocked from this session by a write-action permission rule)
  — either allow that Bash pattern, or run `tigervncserver :1` manually
  from the console/an SSH session and read the real stderr.

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
