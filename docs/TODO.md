# Remaining work

Snapshot as of 2026-09-18. `local-workstation` (the ThinkPad, "PeakPulse")
is currently powered off, so everything below is split so it's clear
what's actionable right now vs. what's waiting on that machine.

---

## remote-workstation (VM, kenny) — available now

- **VS Code Settings Sync isn't actually turned on yet.** We fixed the
  blocker (the keyring-unlock dialog now spawns automatically at session
  start — answer it with a blank password once per boot), but nobody has
  actually run "Settings Sync: Turn On" and signed in with GitHub yet.
  That's a manual, interactive step in the VS Code UI itself.
- **Todo-Tree ripgrep fix just landed, not yet confirmed working.** Open a
  workspace with a `TODO`/`FIXME` comment in it and check the Todo-Tree
  panel actually populates without the "Failed to find vscode-ripgrep"
  error.
- **The keyring-unlock dialog fix has only been confirmed via live D-Bus
  testing, not a full reboot on this host specifically.** It survived a
  VNC-session restart (mate-session restarted, keyring stayed unlocked),
  but hasn't been checked across an actual `reboot`. Worth confirming next
  time this VM reboots for any reason.
- **`docs/README.md` is stale in places** — it still has language from the
  09-15/09-16 planning sessions (e.g. describing `local-workstation` as
  untouched) that predates everything done since. `docs/QUICKSTART.md` is
  current; README could use a reconciliation pass whenever there's a lull.

## local-workstation (laptop, bean) — blocked until it's powered on

Nothing is *broken* here — the last full playbook run (today, before
shutdown) applied and verified cleanly (`ok=157`, `failed=0`). These are
things to check/do once it's back on:

- **ZeroTier** — you said you'd install and configure this yourself on the
  real hardware only (not the VM). `infra-connections.py` already handles
  it gracefully either way (shows "not installed" until it's there, picks
  it up automatically once it exists — no redeploy needed).
- **Confirm the keyring-unlock dialog fix across a real reboot** — this
  host already had one interactive unlock click during testing today, but
  the *proactive autostart trigger* (the actual final fix) should be
  reverified after the next real boot, same as remote-workstation above.
- **VS Code Settings Sync + Todo-Tree** — same as remote-workstation:
  sign-in hasn't happened yet, ripgrep fix not yet confirmed by actually
  opening a project with TODOs in it.
- **Physical GUI login sanity check** — lightdm was enabled/unmasked for
  this host (bare-metal, real console) earlier in this work; worth
  confirming it still comes up cleanly to a MATE desktop after a cold
  boot, not just the warm-restart state it was last verified in.

## General / repo-level

- **No CI yet** — playbooks are only validated by actually running them
  against the two real hosts, not via `--syntax-check`/`ansible-lint` in
  any automated pipeline.
- **`docs/README.md` reconciliation** (see above) — lower priority, purely
  cosmetic/accuracy, `QUICKSTART.md` is the accurate day-to-day reference
  in the meantime.

---

## Already done (for reference — not remaining work)

Everything else from this session is finished and verified on both hosts:
Tailscale IPs in inventory, disk-layout/EFI-partition fix, GPU/sound
drivers (bare-metal only), lightdm gating by `machine_role`, Papirus-Dark
theme, the full dev-applets port (apt-key-refresh/dotfile-sync/trays) with
the Qt6 StatusNotifierWatcher fix, the bean-vs-kenny account split with the
`update_password: on_create` safety fix, the fstab UUID-matching fix, the
full build-dependency audit (fleetwm/.NET/Armbian Build Framework) applied
fleet-wide, VS Code installed with Copilot/chat disabled, Brave/VS Code
`password-store=basic`, and Pulsar/Zed removed from both machines.
