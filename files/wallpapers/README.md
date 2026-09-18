# Per-workstation wallpapers

Drop one image per host here, named after its inventory hostname:

- `local-workstation.<ext>`
- `remote-workstation.<ext>`

Any common image extension works (`.jpg`, `.png`, `.svg`, ...) —
`setup-mate-desktop.yml` looks for `files/wallpapers/{{ inventory_hostname }}.*`
on the controller, deploys whichever one it finds to
`/usr/share/backgrounds/stationctl/` on that host, and points
`org/mate/desktop/background` at it via dconf. No file for a host yet =
that host's wallpaper tasks silently skip (a debug note says so in the
play output) — nothing breaks, it just keeps whatever background is
already set until an image shows up here.

Re-run `ansible-playbook setup-mate-desktop.yml --limit <host> --tags wallpaper`
after adding a file to apply it without re-running the whole desktop setup.
