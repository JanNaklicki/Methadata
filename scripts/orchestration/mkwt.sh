#!/bin/zsh
# użycie: mkwt.sh KARTA BRANCH
set -e
R=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
cd $R
W=$R/.claude/worktrees/$1
[ -d $W ] || git worktree add -q $W -b $2 main
cd $W && rm -rf .venv; /opt/homebrew/Cellar/python@3.14/3.14.4/Frameworks/Python.framework/Versions/3.14/bin/python3.14 -m venv .venv && .venv/bin/pip install -q -e '.[test]' >/dev/null && echo "$1 ok $(git rev-parse --short HEAD)"
