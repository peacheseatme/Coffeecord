#!/usr/bin/env bash
# Publish (or overwrite) a git tag for c-cord update / GitHub Releases.
#
# Usage:
#   ./scripts/ccord_publish_release.sh <version> [--force]
#
# Examples:
#   ./scripts/ccord_publish_release.sh 1.0.4
#   ./scripts/ccord_publish_release.sh 1.0.4 --force   # move tag + force-push
#
# Pushes main, creates tag v<version> (or keeps a leading v), force-pushes the
# tag when --force is set, and creates/updates a GitHub Release when `gh` is
# authenticated (so zipball updates work without a git clone).
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()  { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
info() { echo -e "${CYAN}[INFO]${RESET} $*"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FORCE="false"
VERSION=""
while [[ $# -gt 0 ]]; do
    case "${1}" in
        -f|--force) FORCE="true" ;;
        -h|--help)
            sed -n '2,16p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        -*)
            err "Unknown option: ${1}"
            exit 1
            ;;
        *)
            if [[ -n "${VERSION}" ]]; then
                err "Unexpected extra argument: ${1}"
                exit 1
            fi
            VERSION="${1}"
            ;;
    esac
    shift
done

if [[ -z "${VERSION}" ]]; then
    err "Usage: $0 <version> [--force]"
    exit 1
fi

# Normalize to tag with leading v (c-cord update accepts both).
TAG="${VERSION}"
if [[ "${TAG}" != v* ]]; then
    TAG="v${TAG}"
fi

if [[ ! -d "${ROOT}/.git" ]]; then
    err "Not a git repository: ${ROOT}"
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    err "git is required."
    exit 1
fi

# Detached HEAD (after c-cord update) → return to main before tagging.
if ! git symbolic-ref -q HEAD >/dev/null 2>&1; then
    info "Detached HEAD; checking out main..."
    git checkout -q main
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${BRANCH}" != "main" && "${BRANCH}" != "master" ]]; then
    if [[ "${FORCE}" != "true" ]]; then
        err "Current branch is '${BRANCH}'. Switch to main (or pass --force)."
        exit 1
    fi
    warn "Tagging from branch '${BRANCH}' (--force)."
fi

# Do not commit local Storage / secrets; warn if other tracked files are dirty.
DIRTY_CODE="$(git status --porcelain --untracked-files=no | grep -vE '^( M|M |MM) Storage/' || true)"
if [[ -n "${DIRTY_CODE}" && "${FORCE}" != "true" ]]; then
    err "Working tree has non-Storage changes. Commit/stash them, or pass --force to tag HEAD anyway."
    echo "${DIRTY_CODE}"
    exit 1
fi
if [[ -n "${DIRTY_CODE}" ]]; then
    warn "Non-Storage dirty files present; tagging current HEAD anyway (--force)."
fi

HEAD_SHA="$(git rev-parse --short HEAD)"
info "Pushing ${BRANCH} (${HEAD_SHA})..."
git push -u origin "${BRANCH}"

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1; then
    if [[ "${FORCE}" != "true" ]]; then
        err "Tag ${TAG} already exists. Re-run with --force to move and overwrite it."
        exit 1
    fi
    info "Moving existing tag ${TAG} → ${HEAD_SHA}..."
    git tag -f "${TAG}"
else
    info "Creating tag ${TAG} at ${HEAD_SHA}..."
    git tag "${TAG}"
fi

if [[ "${FORCE}" == "true" ]]; then
    info "Force-pushing tag ${TAG}..."
    git push origin "refs/tags/${TAG}" --force
else
    info "Pushing tag ${TAG}..."
    git push origin "refs/tags/${TAG}"
fi

# GitHub Release (zipball for c-cord update archive path). Optional if gh missing/unauthed.
if command -v gh >/dev/null 2>&1; then
    if gh auth status >/dev/null 2>&1; then
        TITLE="${TAG#v}"
        if gh release view "${TAG}" >/dev/null 2>&1; then
            info "Updating GitHub Release ${TAG}..."
            gh release edit "${TAG}" --title "${TITLE}" --target "$(git rev-parse HEAD)" || \
                warn "gh release edit failed; tag is still pushed."
        else
            info "Creating GitHub Release ${TAG}..."
            gh release create "${TAG}" --title "${TITLE}" --generate-notes || \
                warn "gh release create failed; tag is still pushed. Publish a Release in the GitHub UI for zipball updates."
        fi
    else
        warn "gh is not authenticated (gh auth login). Tag pushed; publish a GitHub Release on ${TAG} for zipball updates."
    fi
else
    warn "gh not installed. Tag pushed; publish a GitHub Release on ${TAG} for zipball updates."
fi

ok "Published ${TAG} from ${BRANCH} @ ${HEAD_SHA}"
echo "  Users: c-cord update ${TITLE:-${TAG#v}}"
echo "  Or:    c-cord update ${TAG}"
