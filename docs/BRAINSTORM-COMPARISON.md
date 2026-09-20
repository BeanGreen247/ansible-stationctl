# Brainstorm — how this setup compares, and what would make it stand out

Research pass done 2026-09-19 via GitHub's search API (`gh search repos`,
`gh api`, authenticated). General web search wasn't usable this round:
DuckDuckGo's HTML/lite endpoints returned a bot-check CAPTCHA from this
machine's egress IP via `curl`, and browser-based search (Playwright/CDP
against a real Chrome in the VNC session) hung indefinitely on every
navigation attempt — `curl` to the same domains worked fine from the
shell, so this looks like a Chrome-renderer-specific network issue in
this VM (possibly DNS-over-HTTPS to a blocked resolver), not a real
connectivity block; not chased further since `gh`'s results were already
solid. GitHub data below is real, live star counts as of this date, not
recalled from training. This file exists in
both `ansible-stationctl` and `ansible-proxmox` (mirrored, same content)
since the comparison spans both repos as one system. Feeds into a future
website writeup — not written yet, holding until the actual site content
is reviewed.

## The landscape, with real numbers

| Project | Stars | What it actually is |
|---|---|---|
| [`community-scripts/ProxmoxVE`](https://github.com/community-scripts/ProxmoxVE) | 29,614 | **The single most relevant comparable project.** One-liner bash scripts (~480 of them) to spin up an LXC/VM for a popular self-hosted app on Proxmox — fast, imperative, run-once. Direct philosophical opposite of this repo's approach (declarative, idempotent, re-runnable Ansible) |
| [`twpayne/chezmoi`](https://github.com/twpayne/chezmoi) | 21,661 | Dotfiles manager — templated config files across machines, not full OS/package state |
| [`nix-community/home-manager`](https://github.com/nix-community/home-manager) | 10,360 | Declarative *user environment* (packages, dotfiles, services) via Nix — reproducible, but a real learning-curve commitment |
| [`geerlingguy/mac-dev-playbook`](https://github.com/geerlingguy/mac-dev-playbook) | 7,047 | The closest well-known analog to this repo's *shape* — Ansible-driven personal machine setup — but macOS-only, dev-tooling focused, no VM/bare-metal parity story |
| [`geerlingguy/ansible-role-docker`](https://github.com/geerlingguy/ansible-role-docker) | 2,292 | Reference point for a well-maintained single-purpose Ansible role (README/testing/CI quality bar) |
| [`rishavnandi/ansible_homelab`](https://github.com/rishavnandi/ansible_homelab) | 399 | Top result for "ansible homelab" specifically — Docker-container homelab bring-up, not VM/desktop provisioning |
| Everything else found searching "ansible homelab/desktop/dotfiles/workstation" | 0–136 | Long tail of personal repos, essentially undiscoverable, no distinguishing README/docs/CI |

**The community-scripts/ProxmoxVE comparison, specifically** — at nearly
30k stars this is the de facto standard people reach for on Proxmox, so
it's worth being precise about how this repo differs rather than just
citing the star count:
- Their scripts are **imperative and one-shot**: run it once, you get a
  configured LXC. Run it again against the same container and it doesn't
  reliably converge to the same state — that's not their design goal.
  This repo's playbooks are **idempotent by design** (and, as of this
  session, actually *verified* idempotent against real hosts, not just
  assumed).
- Their unit is **one app per LXC container**. This repo's unit is **one
  whole workstation** (VM or bare-metal, same environment) — desktop,
  dev tooling, security hardening, kernel tuning, all converging from
  the same source of truth.
- Their strength is breadth and speed for self-hosted *services*
  (Jellyfin, *arr stack, Home Assistant, etc.) — genuinely the better
  tool for "I want Grafana running in five minutes." This repo doesn't
  compete there and shouldn't try to.
- Neither repo's strength invalidates the other — they solve different
  problems (spin up an app vs. maintain a workstation's full state) and
  a real homelab plausibly uses both, which is a more honest framing for
  a website writeup than "better than X."

**The actual gap in the ecosystem**: nothing found combines all of what
this two-repo system does — (1) Proxmox VM *and* bare-metal provisioning
from the same playbook set, with genuine environment parity between a VM
and real hardware, (2) a full desktop environment (not just dotfiles/CLI
tooling) driven the same way, (3) fleet-wide live resource reporting
(`pve_vm_status.yml`), and (4) — as of this session — an IP-drift +
idempotency-verification wrapper that actually *proves* the playbooks
stay correct against real hosts, not just "should work." The closest
comparable projects each do exactly one of these things, not all four.

## Where the big tools are genuinely ahead, worth being honest about

- **chezmoi and home-manager both have first-class secret management
  integrations** (age/gpg encryption built into the tool, 1Password/Bitwarden
  templating) — this repo's Ansible Vault approach is fine but more manual
  (`~/.vault_pass.txt`, `ansible-vault edit`).
- **home-manager is fully reproducible and rollback-able** (Nix generations)
  — nothing here has an equivalent "one command to roll back the whole
  system to yesterday's state." Ansible playbooks are forward-only by
  design; reverting a change means writing the opposite change.
- **chezmoi's templating is per-machine-aware out of the box** (machine-specific
  values via `.chezmoi.yaml`, no separate `host_vars/` concept to learn) —
  lower barrier to entry than Ansible's inventory/group_vars/host_vars
  layering for someone who just wants dotfiles synced.
- **geerlingguy's roles have a documented testing bar this repo doesn't
  fully meet yet**: Molecule (spin up a Docker/VM target, run the role,
  assert the result, tear down) is the standard Ansible testing pattern
  and this repo doesn't use it — `scripts/test_playbooks.sh` only proves
  "loads without syntax errors," not "actually produces the right
  end state." The new idempotency wrapper is a real, distinguishing
  answer to *some* of that gap (proving idempotency against real hosts),
  but it's not the same as Molecule's isolated-environment behavioral
  testing.

## Ideas worth considering, ranked by leverage vs. effort

1. **Molecule scenarios for the highest-risk playbooks** (kernel/perf
   tuning, security hardening) — would catch "this task is logically
   wrong" bugs that syntax-check/lint structurally cannot, closing the
   gap called out above. Higher effort (needs Docker-in-CI or a VM
   target), highest payoff for correctness confidence.
2. **A short top-level README badge/table styled like geerlingguy's roles**
   (build status, supported OS, last-tested-against-real-hardware date) —
   cheap, and directly addresses "this looks like a serious, maintained
   project" versus the long tail of undiscoverable personal repos found
   above.
3. **Publish the idempotency-wrapper pattern as its own thing** (a
   standalone `ansible-idempotency-guard` tool/gist, decoupled from this
   repo's specific playbooks) — genuinely novel relative to everything
   found in this research pass; nothing else combines a live IP-drift
   check with an automatic idempotency-verification-and-lock step. Good
   candidate for the website writeup specifically, since it's the one
   piece here that isn't "another homelab repo," it's a reusable pattern.
4. **A one-command "prove this machine matches its playbooks" report**
   (extend `scripts/run_playbook.py --status` into a real drift report
   across all playbooks, not just the last-run lock state) — closes some
   of the home-manager "I can see the exact current state" gap without
   adopting Nix.
5. **Secrets story upgrade** (age-encrypted vars via `community.sops` or
   similar, replacing the raw `~/.vault_pass.txt` file convention) — lower
   priority, current approach works, but this is the most-repeated
   "better tool does this" pattern in the research above.

## Explicitly not recommended

- **Don't chase chezmoi/home-manager feature parity wholesale.** They
  solve a narrower problem (dotfiles / user environment) extremely well;
  this system's actual differentiator is the VM+bare-metal provisioning
  and fleet-management breadth, which neither tool attempts. Competing on
  their terms would dilute what's actually distinctive here.
- **Don't rewrite in Nix.** Real reproducibility win, but a from-scratch
  rewrite of two working, real-hardware-verified repos for a paradigm
  shift is not a good effort/payoff trade given the current setup already
  works and is actively maintained.
- **Don't try to out-breadth community-scripts/ProxmoxVE's ~480 app
  scripts.** Different problem (one app vs. a whole workstation), and
  they have a 2+ year head start and a large contributor base for that
  specific breadth game. Competing there is a losing trade; the
  idempotent-workstation angle is the one this repo can actually own.

## Open question for the website writeup

Once the actual site content/structure is reviewed: is the angle "here's
a mature homelab-management system" (compete on breadth/completeness) or
"here's a novel idempotency-verification pattern for Ansible" (compete on
one genuinely distinctive technical idea)? The research above suggests
the second is the stronger, more defensible claim — the breadth claim
runs into chezmoi/home-manager's much larger, more polished ecosystems,
but nothing found does live idempotency verification with a halt-and-lock
UX the way this session's `scripts/run_playbook.py` does.
