#!/usr/bin/env bash
# "Unit tests" for this repo's playbooks: the class of failure that can be
# caught WITHOUT a real host (bad YAML, undefined vars, broken Jinja,
# structurally invalid tasks, deprecated/incorrect module args). This
# cannot catch "the task is logically wrong for this specific hardware" —
# that still needs a real run via scripts/run_playbook.py against a real
# host. Run this before every push; CI (.github/workflows/ci.yml) runs it
# on every push/PR too.
set -uo pipefail
cd "$(dirname "$0")/.."

# Some shells leave stdio non-blocking, which ansible-core refuses to run
# under (same issue scripts/run_playbook.py works around).
python3 - <<'EOF'
import fcntl, os
for fd in (0, 1, 2):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
EOF

fail=0

echo "== yamllint =="
if command -v yamllint >/dev/null; then
    yamllint -c .yamllint.yml . || fail=1
else
    echo "SKIP: yamllint not installed"
fi

echo
echo "== ansible-playbook --syntax-check (every top-level playbook) =="
for pb in *.yml; do
    # host_vars/group_vars live in subdirectories, so a plain glob here is
    # already just the top-level playbooks.
    echo "-- $pb --"
    ansible-playbook "$pb" --syntax-check || fail=1
done

echo
echo "== ansible-lint =="
if command -v ansible-lint >/dev/null; then
    ansible-lint || fail=1
else
    echo "SKIP: ansible-lint not installed"
fi

echo
if [ "$fail" -eq 0 ]; then
    echo "PASS: all playbook tests clean."
else
    echo "FAIL: see above."
fi
exit "$fail"
