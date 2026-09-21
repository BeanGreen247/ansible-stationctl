# Remaining work

Snapshot as of 2026-09-18 (updated same day — local-workstation is back on
and the full non-bootstrap playbook set has now run against it end to end).

---

## 2026-09-21: USB mounting broken by a standing VNC session; fixed + toggle added

**Symptom**: USB drives wouldn't mount on `local-workstation` at all — first
a genuinely stale ghost mount from one drive's unclean unplug (fixed), then
a second, real, host-wide cause for every drive: `tigervncserver@:1.service`
runs an **always-on** standing MATE/GVfs session by design (`setup-remote-
access.yml`), and that session's `gvfs-udisks2-volume-monitor` was racing
the physical seat0 session's for every `udisksd` mount request — confirmed
via `polkit`'s journal (`unix-session:c1`, the VNC session, repeatedly
"FAILED to authenticate" for `org.freedesktop.udisks2.filesystem-mount`)
and via `loginctl session-status c1` showing a live `Xtigervnc` + full
`mate-session` process tree. This has nothing to do with `security-
harden.yml` — confirmed by grep, it touches none of polkit/logind/udisks2.

**False-alarm side note, logged for the record**: `loginctl` showed the VNC
session's "Remote" address as `203.0.113.20` — a documentation-reserved
(RFC 5737) IP, not a real client. Timing (session started 39s after boot,
matching `tigervncserver@:1.service` being `enabled`) and zero live TCP
connections on port 5901 both confirm this was the normal always-on
service starting at boot, not an actual external connection. Killed via
`loginctl terminate-session c1` anyway per an explicit "not me, kill it"
from the user, which is what surfaced the real always-on-VNC root cause.

**Fix**: `vnc_service_enabled` (new host_var, defaults `true` — preserves
`remote-workstation`'s existing always-on behavior) — set to `false` in
`host_vars/local-workstation/main.yml`. `setup-remote-access.yml`'s
"Enable and start" task now gates on it, with a new paired "stopped and
disabled" task for when it's false.
- **Caught and fixed a real handler bug in the same pass**: the `Restart
  VNC server` handler ran unconditionally on `vnc_restart_ok`, with no
  check against `vnc_service_enabled` — so ANY config-drift task in the
  same play (e.g. the VNC policy file) would notify it and silently
  **turn VNC back on** immediately after the "stopped and disabled" task
  had just turned it off. Reproduced live on `local-workstation` (service
  went `active`/`disabled` — running again seconds after being stopped).
  Fixed by adding `and (vnc_service_enabled | default(true) | bool)` to
  the handler's `when:`. Verified idempotent afterward: VNC now stays
  `inactive`/`disabled` across repeated `setup-remote-access.yml` runs.
- **New manual toggle**: `deploy_vnc_toggle_launcher: true` (also set only
  in `local-workstation/main.yml`) deploys `/usr/local/bin/vnc-toggle.sh`
  (checks current state, `pkexec systemctl start/stop
  tigervncserver@:1.service`, `notify-send`s the result) plus a MATE menu
  launcher (`/usr/share/applications/vnc-toggle.desktop`, "Toggle Remote
  Desktop (VNC)"). Uses `pkexec` deliberately instead of a NOPASSWD
  sudoers entry — stays behind the normal polkit admin-auth prompt each
  use rather than adding a standing passwordless privilege for something
  this occasional.
- **Current state**: VNC is OFF on `local-workstation` until manually
  started (via the new launcher or `sudo systemctl start
  tigervncserver@:1.service`). `remote-workstation` is completely
  unaffected — verified `--check --diff` there shows zero changes.
- **Follow-up same session**: the "ask before restarting VNC" GUI safety
  dialog (installed for VNC-primary hosts like `remote-workstation`) fired
  on `local-workstation` too, correctly per its own design (any detected
  `mate-session`, physical or VNC, triggers it) but pointlessly there —
  VNC is off by default and only ever toggled manually via `pkexec`
  (`vnc-toggle.sh`), never through this playbook's restart handler, so
  the dialog had nothing meaningful to protect. New host_var
  `vnc_restart_safety_gate_enabled` (default `true`, set `false` only in
  `local-workstation/main.yml`) skips the whole MATE-session-check/dialog
  chain on that host. Verified with a real (non-`--check`, since
  command/shell tasks always report `skipping` under `--check` regardless
  — a `--check` dry run of this specific change would have been
  meaningless) run: dialog tasks skip cleanly, VNC stays
  `inactive`/`disabled` throughout. `remote-workstation` keeps the dialog
  unchanged.
- **`remote-workstation` gets its own launcher too, but restart-only**:
  VNC is always-on there, so a start/stop toggle makes no sense — added
  `deploy_vnc_restart_launcher: true` (its own host_var,
  `remote-workstation/main.yml` only) deploying
  `/usr/local/bin/vnc-restart.sh` (`pkexec systemctl restart
  tigervncserver@:1.service` + `notify-send`) and a "Restart Remote
  Desktop (VNC)" MATE menu launcher. Applied with `--tags
  restart-launcher` only, so the VNC service itself and the safety-gate
  dialog were untouched — verified `active` before and after.

## 2026-09-21: security-harden.yml applied to remote-workstation + Lynis triage round 2

- **`remote-workstation` now hardened too** — `ansible-playbook
  security-harden.yml --limit remote-workstation` run in full:
  `ok=68, changed=43, failed=0`. Verified with a fresh `ansible ... -m
  ping` afterward (`pong`, sshd restart didn't lock anything out).
  The earlier caution below (about running this from that same VM) no
  longer applies — this was run from local-workstation instead.
- **Lynis audit run on both hosts** (`sudo lynis audit system`; remote
  run by the user directly since this session's Bash tool has no TTY
  for interactive sudo). Remote hardening_index=75/100 before round 2.
  Local scan not yet completed the same way — still blocked on the same
  no-TTY-sudo limitation; needs the user to run it interactively again
  and report back, or set up a passwordless sudo/askpass path for `bean`
  if this is going to be routine.
- **Round 2 Lynis fixes added to `security-harden.yml`** (new `[CIS 7.2]`
  tasks, same `cis7` tag as round 1) and applied to **both** hosts,
  local-workstation first: `vm.swappiness=60` [FILE-6394], `apt-listbugs`
  install [DEB-0810], `acct` process accounting [ACCT-9622], `sysstat`
  activity accounting enabled [ACCT-9626]. Deliberately skipped (same
  reasoning as round 1's SSH-session-limit note): GRUB bootloader
  password (lockout risk, no console fallback on either host), USB/
  firewire storage driver disable (both are workstations people plug
  drives into), compiler lockdown, AIDE/file-integrity tooling, external
  syslog host. PKGS-7370 (debsums cron) needed no task — the package
  (installed in round 1) ships its own enabled cron.daily hook.
- **Still open**: `PKGS-7392` (vulnerable/outdated packages) — an
  `apt-get dist-upgrade` across both hosts was blocked by the Claude Code
  auto-mode classifier as too broad to run unattended. Needs the user to
  run it manually (or explicitly authorize it) on each host:
  `sudo apt update && sudo apt dist-upgrade && sudo apt autoremove`.
  Both hosts already have `unattended-upgrades` installed per the Lynis
  report, so this may self-resolve on its own schedule regardless.
- **Local Lynis scan + fix-up still pending** per the note above — once
  the user reports it done, fetch `/var/log/lynis-report.dat` the same
  way remote's was (or read it directly on this host) and triage the
  same way: local-first was the intent, but remote's scan finished first
  so remote got triaged first this round.

### Follow-up: remote-workstation re-scanned after round 2, one more fix applied

Re-ran `sudo lynis audit system` on `remote-workstation` after round 2:
`hardening_index` 75 → 76, the `PKGS-7392` vulnerable-packages warning is
gone (user ran the apt upgrade manually), and all four round-2 suggestions
(swappiness/apt-listbugs/acct/sysstat/debsums-cron) dropped off the list —
confirms round 2 worked as intended.

- **One more trivial fix applied and synced to both repos**: `BANN-7126`/
  `BANN-7130` — Lynis wants ≥5 legal keywords in `/etc/issue` /
  `/etc/issue.net`, `cis_login_banner` only had 4 ("access", "authori",
  "monitor", "report"). Added a line ("Unauthorized use is prohibited and
  may be subject to prosecution.") to `cis_login_banner` in both plays'
  `vars:` blocks (Debian play + Alpine play) — adds "prohibit" and
  "prosecut" as keywords. Applied to local-workstation then
  remote-workstation (`--tags cis1`), `changed=3` on both (motd, issue,
  issue.net), no other diffs.
- **`ACCT-9628` (auditd not running) investigated and confirmed
  intentional, NOT a bug to fix**: both hosts boot with `audit=0` on the
  kernel cmdline (`systemctl status auditd` shows
  `ConditionKernelCommandLine=!audit=0` unmet, condition skip, not a
  crash). This comes from `setup-kernel-perf-tuning.yml`'s
  `GRUB_CMDLINE_LINUX_DEFAULT` — the task's own comment says `audit=0:
  disable kernel audit subsystem (no compliance requirement on these
  boxes)`. Confirmed via `/proc/cmdline` on both hosts. Fixing this would
  mean editing GRUB + `update-grub` + a reboot on both workstations,
  which directly conflicts with an existing, deliberate performance-
  tuning decision — **not changing this without the user explicitly
  choosing to trade that tuning for a working auditd**, which would need
  its own separate go-ahead given the reboot + perf tradeoff.
- **Remaining Lynis suggestions on remote-workstation are all
  already-deferred items** (see round 1 + round 2 notes above): GRUB
  bootloader password, USB/firewire storage driver disable, SSH
  port/session-limit/TCPKeepAlive/AllowAgentForwarding tweaks, compiler
  lockdown, AIDE/file-integrity tooling, external syslog host, plus a
  few purely informational ones (DNS domain check, unused iptables
  rules, deleted-files-in-use, `KRNL-6000` sysctl profile diff,
  `FILE-6310` separate `/home`/`/var` partitions, `BOOT-5180`/`BOOT-5264`
  informational service-hardening pointers). None of these get
  auto-applied — same reasoning as before (lockout risk, breaks
  legitimate USB use, or is genuinely informational-only with no safe
  automated fix).
- **Local Lynis scan is still the one open item** from this whole
  thread — same no-TTY-sudo blocker as before. Once it's run and the
  report exists at `/var/log/lynis-report.dat` on local-workstation,
  fetch/read it and diff against remote's now-known-good baseline above
  rather than starting the triage from scratch.

### Follow-up: round 3 — pushed hardening_index up, user-scoped tradeoffs

User asked to push the Lynis `hardening_index` as high as possible while
keeping "all or most stuff" working. Asked explicitly (AskUserQuestion)
which of the remaining higher-risk tradeoffs to accept — **only SSH
session tightening was approved**; GRUB bootloader password, AIDE
file-integrity monitoring, and USB/firewire storage lockout were all
declined and are NOT implemented. Don't add those without a fresh
explicit ask.

Applied to both hosts (local-workstation first, then remote-workstation,
each verified reachable via `ansible ... -m ping` after every SSH
change):
- **SHA512 password hashing rounds** (`CIS 6.3.4`, new task):
  `SHA_CRYPT_MIN_ROUNDS 100000` / `SHA_CRYPT_MAX_ROUNDS 200000` in
  `/etc/login.defs` — fixes `AUTH-9229`/`AUTH-9230`. Only affects
  passwords set/changed going forward, not existing hashes.
- **Two `KRNL-6000` sysctl fixes**: `dev.tty.ldisc_autoload=0`,
  `fs.protected_fifos=2`. Added to both the main sysctl block and the
  LXC host-namespace-key cleanup loop for consistency with
  ansible-proxmox's actual LXCs.
- **SSH session tightening** (user-approved): `MaxSessions` 4→2,
  `ClientAliveCountMax` 3→2 (idle cutoff now 30s not 45s), added
  `TCPKeepAlive no` and `AllowAgentForwarding no`. Fixes the rest of
  `SSH-7408`.
- **Real bug found and fixed while doing this, unrelated to the Lynis
  index itself**: `X11Forwarding` was never actually disabled on either
  host, this whole time, despite the CIS block and hardening summary
  both claiming it was. Root cause: Debian's stock `/etc/ssh/sshd_config`
  ships an uncommented `X11Forwarding yes` ABOVE where our CIS block used
  to land (`insertbefore: "^Match "`), and OpenSSH honors the FIRST
  occurrence of a directive — so the stock line silently won. Confirmed
  via `sshd -T` on remote-workstation showing `x11forwarding=yes` despite
  every other directive in the block taking effect correctly. **Fix**:
  changed `insertbefore` to `BOF` (top of file) so our block always wins
  over both the stock file body and anything pulled in via the early
  `Include /etc/ssh/sshd_config.d/*.conf` line. Since an already-existing
  marked block doesn't relocate on its own (Ansible's `blockinfile` only
  honors `insertbefore`/`insertafter` when first creating a block), this
  needed a one-time manual removal of the existing block
  (`ansible ... -m blockinfile ... state=absent`) on each host before
  re-running the playbook to recreate it at the top — done on both hosts,
  verified via `sshd -T` showing `x11forwarding=no` afterward on both.
  **Also caught and fixed as a side effect**: relocating to BOF flipped
  which `MaxAuthTries` value won (stock's stricter `3` had been beating
  our looser `4` the same way) — tightened ours to `3` to match and
  remove the now-newly-visible Lynis suggestion for it.
- **First attempt at the X11Forwarding fix was wrong and reverted before
  applying**: tried a `lineinfile: state=absent` task to strip the stock
  line, but `state=absent` removes ALL matching lines including our own
  (already-applied) directive on every re-run — would have made the
  whole SSH block flap `changed=true` forever and broken the playbook's
  documented idempotency guarantee. Caught via `--check --diff` before
  it was ever applied for real. The `insertbefore: BOF` approach avoids
  this entirely since blockinfile's own idempotency handles it correctly.
- **Result**: remote-workstation's `hardening_index` went 75 → 76 (round
  2) → 81 (round 3 first pass) → 82 (after the MaxAuthTries follow-up).
  Re-scanned after each apply to confirm; SSH stayed reachable via
  `ansible -m ping` after every change, no lockouts.
- **Local-workstation got the exact same round 3 changes applied** but
  has NOT had a fresh Lynis re-scan yet (same pending local-scan item as
  above) — presumed to land at a similar or identical index once scanned,
  since both hosts are provisioned identically by this same playbook.
- **User asked to push toward "almost 100" after seeing 82** — re-asked
  explicitly (AskUserQuestion) whether to now implement the items
  declined in round 3 (GRUB bootloader password, AIDE file integrity
  monitoring, USB/firewire storage lockout) plus a new one surfaced for
  this ask (AUTH-9282 password expiry dates on existing accounts —
  lockout risk for bean/kenny's own logins). **User chose to stop at 82
  and not implement any of these.** Treat 82/100 as the settled target
  for remote-workstation (and presumably local-workstation once scanned)
  unless the user explicitly revisits this.

## 2026-09-21 (earlier): security-harden.yml consolidated with ansible-proxmox

`setup-security-hardening.yml` is gone — replaced by `security-harden.yml`,
kept byte-for-byte identical to ansible-proxmox's copy of the same file
(no symlink/submodule; copy by hand to the other repo when either changes).
See `docs/FIREWALL-PORTS.md` for the per-host port reference and how to
add a new one.

- **Applied to `local-workstation`**: `ok=68, changed=40, failed=0`.
  Verified after: SSH/ping reachable, UFW active (default deny-in, 22+5901
  allowed), fail2ban active, TigerVNC still listening. This was the first
  firewall this host has ever had.
- **Two bugs found (via `--check --diff`, before either could cause real
  damage) and fixed in both repos' copies of the file**:
  1. `cis_preserve_services` was a play var, and Ansible's precedence puts
     play vars above host_vars — so a host_vars override of it was always
     silently ignored. This had *already* let a real run purge `vsftpd`
     entirely off two ansible-proxmox hosts before this was caught. Fixed
     via a `cis_preserve_services_baseline` + `cis_preserve_services_extra`
     merge that host_vars can actually affect. (FTP restored on those two
     hosts via ansible-proxmox's new `restore-vsftpd.yml`.)
  2. `_is_vm`/`_is_lxc` used `groups['vms']`/`groups['lxcs']` directly,
     which throws instead of evaluating false in this repo's inventory
     (only `workstations` exists here) — hit applying to
     `local-workstation` for the first time. Fixed with `groups.get(x, [])`.
- Also reconciled ansible-proxmox's per-host `cis_ufw_extra_ports` against
  live `ss`/`ufw status` + each VM's own Proxmox Notes field across all 10
  of its LXCs/VMs — not relevant to this repo's own two hosts beyond the
  VNC port already declared, but the same audit method is documented in
  `docs/FIREWALL-PORTS.md` if this repo's hosts ever need it again.

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
  through `security-harden.yml`, skipping `setup-ansibleuser.yml`
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
subset (`security-harden.yml`), plus everything under "done this
session" above.
