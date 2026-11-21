#!/usr/bin/env bash
# Loads the online database of one target: the owner runs it, with absolute paths.
#
#   bash /abs/path/cloud/load-remote.sh <preview|real> <schema|texts|work> [--dry-run]
#
#   schema  applies the migrations (the tables)
#   texts   loads every cloud/.build/texts-NNN.json (the books; safe to repeat any time)
#   work    loads the seed of the owner's work (cloud/.build/work-NNN.json); replaces ALL online work, so it first asks
#           the owner to type the target's name
#   --dry-run  prints the commands and runs none (and does not ask)
#
# The database id comes from cloud/deploy.json (DEPLOY_JSON names another file). `python3 tr.py cloud` makes the
# .build files first. Stops at the first failing command with that command's exit code. Bash 3.2 compatible.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-}"
what="${2:-}"
dry=0
if [ "${3:-}" = "--dry-run" ]; then dry=1; elif [ -n "${3:-}" ]; then echo "Unknown option: $3" >&2; exit 64; fi

case "$target" in preview | real) ;; *) echo "Usage: bash load-remote.sh <preview|real> <schema|texts|work> [--dry-run]" >&2; exit 64 ;; esac
case "$what" in schema | texts | work) ;; *) echo "Usage: bash load-remote.sh <preview|real> <schema|texts|work> [--dry-run]" >&2; exit 64 ;; esac

deploy="${DEPLOY_JSON:-$here/deploy.json}"
if [ ! -f "$deploy" ]; then
  echo "Missing $deploy: copy deploy.example.json to deploy.json and fill it in." >&2
  exit 66
fi
dbid="$(python3 -I -c 'import json,sys; print((json.load(open(sys.argv[1])).get(sys.argv[2]) or {}).get("databaseId","").strip())' "$deploy" "$target")"
if [ -z "$dbid" ]; then
  echo "\"databaseId\" of \"$target\" is empty in $deploy." >&2
  exit 66
fi

started="$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Started $started. Target: $target. Step: $what."

files=()   # the batch files; for "schema" the one step is the migration itself
case "$what" in
  schema)
    files=("migrations")
    ;;
  texts | work)
    for f in "$here"/.build/$what-[0-9][0-9][0-9].json; do
      [ -e "$f" ] && files+=("$f")
    done
    if [ "${#files[@]}" -eq 0 ]; then
      echo "No $what-NNN.json in $here/.build: run python3 tr.py cloud from the project's root first." >&2
      exit 66
    fi
    ;;
esac

if [ "$what" = "work" ]; then
  echo "WARNING: this replaces ALL the online work of \"$target\" (translations, memos, titles, glossary) with the files in $here/.build."
  if [ "$dry" -eq 1 ]; then
    echo "(dry run: would ask you to type \"$target\" here)"
  else
    printf 'Type %s to continue: ' "$target"
    read -r answer
    if [ "$answer" != "$target" ]; then
      echo "Not confirmed; nothing was loaded." >&2
      exit 1
    fi
  fi
fi

total="${#files[@]}"
k=0
for f in "${files[@]}"; do
  k=$((k + 1))
  echo "file $k of $total ($(date '+%H:%M:%S')):"
  if [ "$what" = "schema" ]; then
    echo "  cd $here && pnpm exec cf d1 migrations apply $dbid"
    [ "$dry" -eq 1 ] && continue
    (cd "$here" && pnpm exec cf d1 migrations apply "$dbid")
  else
    echo "  cd $here && pnpm exec cf d1 raw $dbid --batch @$f"
    [ "$dry" -eq 1 ] && continue
    (cd "$here" && pnpm exec cf d1 raw "$dbid" --batch "@$f")
  fi
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "Failed at file $k of $total with exit code $rc; stopped." >&2
    exit "$rc"
  fi
done
if [ "$dry" -eq 1 ]; then echo "Done (dry run: nothing ran)."; else echo "Done $total of $total ($(date '+%Y-%m-%d %H:%M:%S %Z'))."; fi
