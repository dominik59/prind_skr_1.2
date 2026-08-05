#!/usr/bin/env bash
# Merge the selected prind upstream branch into the current branch.
# A clean working tree is required so local printer configuration is never
# mixed with an unrelated change.

set -euo pipefail

UPSTREAM_URL="https://github.com/mkuf/prind.git"
REMOTE="prind-upstream"
BRANCH="main"
COMMIT_MERGE=true
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: scripts/merge-upstream.sh [options]

Fetch and merge the official prind upstream into the current branch.

Options:
  --no-commit          Leave a successful merge staged for review.
  --dry-run            Fetch and show incoming commits without merging.
  --remote NAME        Remote name to create/use (default: prind-upstream).
  --branch NAME        Upstream branch to merge (default: main).
  -h, --help           Show this help.

The script refuses to run with tracked local changes or an in-progress merge.
Before a merge it creates a backup branch named
backup/pre-upstream-YYYYmmdd-HHMMSS. On conflicts it leaves Git's merge state
intact: resolve the files, run "git add <files>", then "git commit"; or run
"git merge --abort" to return to the state before the merge.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --no-commit)
      COMMIT_MERGE=false
      ;;
    --dry-run)
      DRY_RUN=true
      ;;
    --remote)
      shift
      (($#)) || die "--remote requires a name."
      REMOTE="$1"
      ;;
    --branch)
      shift
      (($#)) || die "--branch requires a name."
      BRANCH="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "git is required."

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPOSITORY_ROOT=$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null) \
  || die "The script must be located inside a Git working tree."
cd "$REPOSITORY_ROOT"

[[ -z $(git status --porcelain --untracked-files=no) ]] \
  || die "Tracked local changes are present. Commit or stash them before merging."

[[ -z $(git rev-parse -q --verify MERGE_HEAD) ]] \
  || die "A merge is already in progress. Finish it or run 'git merge --abort'."

CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD) \
  || die "HEAD is detached. Check out a branch before merging."

if git remote get-url "$REMOTE" >/dev/null 2>&1; then
  CURRENT_URL=$(git remote get-url "$REMOTE")
  [[ "$CURRENT_URL" == "$UPSTREAM_URL" ]] \
    || die "Remote '$REMOTE' points to '$CURRENT_URL', not '$UPSTREAM_URL'."
else
  git remote add "$REMOTE" "$UPSTREAM_URL"
  printf "Added remote '%s'.\n" "$REMOTE"
fi

printf "Fetching %s/%s...\n" "$REMOTE" "$BRANCH"
git fetch --prune "$REMOTE"
TARGET="$REMOTE/$BRANCH"
git show-ref --verify --quiet "refs/remotes/$TARGET" \
  || die "Upstream branch '$TARGET' was not found."

if git merge-base --is-ancestor "$TARGET" HEAD; then
  printf "Already up to date: %s is contained in %s.\n" "$TARGET" "$CURRENT_BRANCH"
  exit 0
fi

printf "Incoming commits from %s:\n" "$TARGET"
git log --oneline "HEAD..$TARGET"

if "$DRY_RUN"; then
  printf "Dry run complete; no merge was performed.\n"
  exit 0
fi

BACKUP_BRANCH="backup/pre-upstream-$(date +%Y%m%d-%H%M%S)"
git branch "$BACKUP_BRANCH"
printf "Created safety branch '%s'.\n" "$BACKUP_BRANCH"

if "$COMMIT_MERGE"; then
  MERGE_ARGS=(--no-ff --no-edit "$TARGET")
else
  MERGE_ARGS=(--no-ff --no-commit "$TARGET")
fi

if git merge "${MERGE_ARGS[@]}"; then
  if "$COMMIT_MERGE"; then
    printf "Successfully merged %s into %s.\n" "$TARGET" "$CURRENT_BRANCH"
  else
    printf "Merge is staged for review. Inspect it, then run 'git commit'.\n"
  fi
  exit 0
fi

printf '\nMerge conflict detected. Your local configuration was not overwritten.\n' >&2
printf 'Conflicted files:\n' >&2
git diff --name-only --diff-filter=U >&2
printf "\nResolve conflicts, run 'git add <files>', then 'git commit'.\n" >&2
printf "To discard this attempt, run 'git merge --abort'.\n" >&2
exit 1

