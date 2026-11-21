#!/bin/zsh
R=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
cd $R
if ! git merge --no-ff $2 -m "Scal $1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -q; then
  if [ "$(git diff --name-only --diff-filter=U)" = "pyproject.toml" ]; then
    sed -i '' '/^<<<<<<< /d; /^=======$/d; /^>>>>>>> /d' pyproject.toml; .venv/bin/python -c "import tomllib;tomllib.load(open('pyproject.toml','rb'))" || { echo "TOML ZEPSUTY po sumie"; exit 1; }; git add pyproject.toml; git commit -q --no-edit; echo "pyproject: auto-suma"; sed -n '/optional-dependencies/,/^\[tool/p' pyproject.toml
  else echo KONFLIKT; git status -s | grep -v '^A '; exit 1; fi
fi
.venv/bin/pip install -q -e '.[test,media,pdf,ocr,web,graph,density,perceptual]' 2>&1 | grep -iv pip; .venv/bin/pytest 2>&1 | tail -1; git log --oneline -1
