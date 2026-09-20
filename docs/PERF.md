# PERF.md — performance/memory tuning ledger

Records tuning ideas tried on this fleet: kept, rejected, or reverted, and why.
Check here before proposing a tuning change — a "new" idea may already have
been tried and rejected, and this file is where that gets remembered instead
of only living in one person's head or a deleted comment.

Add an entry any time a tuning change is made or considered and dropped.
Format: idea, what was measured (or the reasoning if no formal measurement
was taken — not everything here has been through a full profiling pass),
verdict, why.

| Idea | Host(s) | Verdict | Why |
|---|---|---|---|
| zswap stacked on top of zram | homelab Proxmox host (Starhaven), predates this repo | rejected | Real decompress/recompress churn observed in production — two compression layers fighting each other. Carried into this repo's `setup-performance-tuning.yml` as "single compression layer only" policy; never re-tried here. |
| Background/forced reclaim daemon (KSM-style) on guest workstations | homelab Proxmox host reference doc | rejected | Fights KSM, evicts hot pages. Not even applicable here — KSM is a hypervisor-level concern, not a guest workstation one — but the "don't force reclaim" lesson carried over anyway. |
| Periodic `drop_caches` timer | remote-workstation | rejected | At `vm.vfs_cache_pressure=500`, the kernel already reclaims clean page cache fast enough on its own; the forced timer was redundant. Left available as an opt-in var (`workstation_dropcaches_interval_min`) for a host where that's not true, just not enabled by default. |
| `preload` on all workstations unconditionally | remote-workstation, local-workstation | corrected, not reverted | Originally justified by an assumption that both hosts were HDD-backed. local-workstation is actually SSD — preload's readahead-prediction benefit doesn't apply there, and it's still a persistent daemon writing a stats DB. Fixed by gating install/enable on the new mandatory `workstation_drives` host_vars map instead of a comment-level assumption. See `setup-kernel-perf-tuning.yml`. |
| CPU governor forced to `performance` on every host | remote-workstation, local-workstation | corrected | Fine for a VM with no battery/thermal cost. Wrong default for local-workstation, a real laptop on battery — pinned max clock 24/7 costs battery life and runs hotter for a desktop workload that isn't CPU-bound. Now resolved per `machine_role` (`vm` → `performance`, `bare-metal` → `schedutil`), overridable via `workstation_cpu_governor`. |
| Disable avahi-daemon | remote-workstation, local-workstation | kept, unmeasured | No reachable use case on a VNC-only remote desktop or single-purpose laptop workstation — mDNS/DNS-SD has nothing to discover here. Low-risk, not yet measured for actual idle-RAM/CPU delta; if it turns out to matter to something (e.g. local network printer/service discovery is wanted later), revisit. |
| Cap Docker's `json-file` log driver (`max-size`/`max-file`) | both (dev tooling hosts) | kept | Not a perf win so much as a disk-usage bound — same class of fix as journald's `SystemMaxUse`. Prevents an unbounded log file from a chatty/long-running container on a small-disk workstation. No regression risk; this is a resource-cap correctness fix, not a speed optimization, so it didn't need a before/after benchmark. |
| Scheduled window + `Remove-Unused-Dependencies` for unattended-upgrades | both | deferred indefinitely | Not a tuning question — user doesn't want automatic package updates on this fleet at all ("we will update when we need to and want to"). A separate playbook in `ansible-proxmox` owns update timing; that repo's `hosts.ini` just needs the actual host entries added. Not this repo's concern going forward — don't re-propose scheduling/auto-upgrade tuning here. |
| Bump rotational-disk readahead to 1MB (`queue/read_ahead_kb`) via the existing IO-scheduler udev rule | remote-workstation (HDD, `workstation_drives`) | kept, unmeasured | Kernel default (128KB) is conservative for sequential reads on real rotational media; pairs with BFQ instead of fighting it. Not yet measured with a before/after sequential-read benchmark — flag for follow-up if it doesn't hold up. |
| Disable `mate-power-manager` autostart on VMs, keep on real hardware | remote-workstation (disabled), local-workstation (kept) | kept | Same shape as the CPU-governor split: gated on `machine_role`, not removed unconditionally. Polls upower for battery/AC status the VM doesn't have; genuinely needed on the ThinkPad. |
| Tune earlyoom `--avoid`/`--prefer` process patterns instead of stock config | both | kept, unmeasured | Stock earlyoom config has no opinion about which process is worth saving on this specific session shape (TigerVNC + MATE, not a generic desktop) — an untuned backstop can kill the very session you'd use to recover. `--avoid` protects Xorg/Xtigervnc/sshd/dbus/mate-session/lightdm; `--prefer` targets browser/docker/node/java as the likely actual runaway. Not measured against a real OOM event yet — that's inherently hard to test safely, so this is a reasoned default, not a benchmarked one. |
| Set zram `STREAMS` to match vCPU count | — | rejected, not implemented | Modern kernels (≥4.7, well before Debian 13's kernel) removed the `max_comp_streams` tunable entirely — zram automatically uses one compression stream per online CPU with no config surface for it, and zram-tools' `/etc/default/zramswap` has no `STREAMS` key to begin with. Setting it would be a no-op line, not a real optimization. Don't re-propose this without first confirming the target kernel actually exposes the knob. |
| VS Code Python language server: Pylance → Jedi | remote-workstation | kept, measured | See [VS Code editor tuning](#vs-code-editor-tuning) below — 455MB → 56MB on the same repo, ~8x reduction on the single biggest per-window memory line item. |
| VS Code AI/Copilot lockdown (`extensions.allowed`, `chat.disableAIFeatures`) | both (synced via account, not this repo) | kept, measured | See [VS Code editor tuning](#vs-code-editor-tuning) below — this VS Code build ships a bundled Copilot binary that spawns unprompted on a stock profile (~238MB); blocked entirely on the tuned profile. |

## VS Code editor tuning

Unlike everything else in this file, most of this tuning does **not** live in
this repo's playbooks — it lives in `settings.json`, synced across every
machine via the end user's own GitHub-account Settings Sync. `setup-dev-tools.yml`
only owns the two things Settings Sync doesn't cover: `argv.json`'s
`password-store: basic` (keychain workaround) and one-time removal of VS
Code's built-in "Agents" profile (`~/.config/Code/User/profiles/builtin/agents`
+ its `globalStorage/storage.json` entry — confirmed on 2026-09-18 this
doesn't get recreated once the Agents/Chat feature is disabled). Re-running
the playbook must never fight the synced `settings.json` — see the comment
block directly above the `Install dev tool packages` task in
`setup-dev-tools.yml` if that constraint ever needs revisiting.

**Measured, not estimated** (both runs against this very repo,
`ansible-stationctl`, on remote-workstation):

| | Stock/empty profile | Tuned profile |
|---|---|---|
| Copilot process | Spawns unprompted, ~238MB (this VS Code build bundles a native Copilot binary outside the normal extension system — `extensions.allowed` blocks the marketplace extension IDs but this binary isn't one; blocked instead by `chat.disableAIFeatures`) | Not present |
| Python language server | Pylance, ~455MB fixed floor regardless of project size (confirmed against a near-empty 3.2MB test project too) | Jedi (bundled in `ms-python.python`, no install needed), ~56MB — ~8x smaller |
| Telemetry / crash reporting | On | Off |
| Terminal sessions | Persisted across restarts (extra ~118MB utility-host process just to hold a restored shell) | Not persisted |
| File watcher scope | Default excludes only | Explicit excludes for `.git/objects`, `node_modules`, `dist`, `build`, `.venv`, `coverage`, `.cache`, `tmp` |
| Workspace auto-scanning | npm/task/grunt/gulp/jake script detection on by default | All off |
| Total window RSS on this repo | ~1.5–1.8GB (before Pylance even activates on a `.py` file — would climb further once it does) | ~1.4–1.8GB (Jedi already active, Copilot absent) — run-to-run variance here comes from VS Code's own extension-activation timing (JSON schema validation, Ansible/YAML language features), not from the tuning itself |

The total-RSS row has real variance between runs (VS Code doesn't activate
every extension in the same order every time), which is why it's a range
rather than one number — the two rows above it (Copilot presence, Pylance vs.
Jedi) are the clean, reproducible, apples-to-apples deltas and the actual
reason the range is lower on the tuned side. Full config lives in the user's
synced `settings.json` — see [`setup-dev-tools.yml`](../setup-dev-tools.yml)'s
VS Code section comments for what this repo does vs. doesn't own.

## Open items (not yet decided/measured)

- `fstrim.timer` — considered, but both current hosts are HDD-backed per `workstation_drives` (host_vars), so periodic TRIM doesn't apply to this fleet today. Revisit if/when an SSD/NVMe host is added — gate it the same way preload is gated, off `_stationctl_has_ssd`.
- Readahead bump (1MB on rotational) and the earlyoom avoid/prefer tuning above are both "reasoned, not measured" — no before/after benchmark exists yet for either. Worth a real measurement pass (sequential read throughput for readahead; can't safely benchmark earlyoom's kill selection without inducing real OOM pressure) before calling either one settled.
