#!/bin/zsh
# gen.sh KARTA -> tworzy worktree i plik promptu; wypisuje branch
T=$(cd "$(dirname "$0")" && pwd); R=$(git -C "$T" rev-parse --show-toplevel)
blk=$(awk -v c="### $1 " 'index($0,c)==1{f=1} f&&/^```text/{g=1;next} g&&/^```/{exit} g{print}' $R/WIADOMOSCI_DO_MODELI.md)
br=$(echo "$blk" | grep -o 'codex/[a-z0-9-]*' | head -1)
task=$(echo "$blk" | sed -n 2p)
$T/mkwt.sh $1 $br 2>&1 | grep -iv pip
python3 -c "import sys;t=open('$T/prompt.txt').read();print(t.replace('{CARD}',sys.argv[1]).replace('{BRANCH}',sys.argv[2]).replace('{TASK}',sys.argv[3]),end='')" $1 $br "$task" > ${TMPDIR:-/tmp}/p_$1.txt
echo "$1 $br ${TMPDIR:-/tmp}/p_$1.txt"
