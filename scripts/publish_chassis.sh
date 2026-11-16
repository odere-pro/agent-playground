#!/usr/bin/env bash
# Build and push the default chassis image, then pin its digest in the library chart (139 CH-19).
#
#   scripts/publish_chassis.sh plan            print the steps of `publish`; runs nothing
#   scripts/publish_chassis.sh publish         build, push vX.Y.Z, write the digest into the chart
#   scripts/publish_chassis.sh pin DIGEST      write DIGEST into the chart only (no Docker)
#   scripts/publish_chassis.sh sign DIGEST     sign DIGEST (cosign, by digest), then pin it
#
# If `publish` pushed but the signature failed, the image is on the registry unsigned and the
# chart is not pinned. The log prints the digest and the command that finishes the job:
#   scripts/publish_chassis.sh sign sha256:<the pushed digest>
# Never rerun `publish` for this: the tag exists now, so it refuses.
#
# The image is ghcr.io/odere-pro/agent-chassis (decided 2026-11-15). The tag is
# packages/chassis/pyproject.toml's version with a `v`; the library chart's `chassis.image.tag`
# (deploy/helm/agent-lib/templates/_values.tpl) must already say the same, or `publish` stops.
# The digest is the multi-platform index digest buildx reports after the push. It replaces the
# chart's `chassis.image.digest` line; the orchestrator commits that change.
#
# Before a push it refuses: a tag that is already on the registry (a rerun never overwrites a
# published tag), a HEAD that is not on origin/main, and a tree with changes. The tag check fails
# closed: only a "not found" answer counts as absent; any other registry error stops the run. The build sets
# SOURCE_DATE_EPOCH from the HEAD commit and adds an SBOM and max provenance. After the push it
# signs the image by digest, never by tag: `cosign sign --yes`, keyless, which opens a browser for
# the OIDC login on the user's Mac. Checking the signature at admission is 056 CH-7's.
#
# Credentials: this script never logs in and reads no token. The user logs in first, with a
# classic PAT that has only the write:packages scope (not `gh auth token`, which carries more):
#   docker login ghcr.io -u odere-pro --password-stdin   (paste the PAT on stdin)
# and runs `docker logout ghcr.io` after. A new GHCR package is private: a cluster pulls it only
# with an imagePullSecret, or after the package is made public in its GitHub settings.
#
# suggested: linux/amd64 and linux/arm64 (CI and kind on a Mac); a buildx builder that can push a
# multi-platform image (`docker buildx create --use` once, if the default builder cannot).
# PUBLISH_DRY_RUN=1 prints each command instead of running it, as `plan` does.
# CHASSIS_VALUES_TPL points `pin` and `sign` at another chart file (tests); `publish` ignores it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=ghcr.io/odere-pro/agent-chassis
SOURCE=https://github.com/odere-pro/agent-playground
PLATFORMS=linux/amd64,linux/arm64
VALUES_TPL=$ROOT/deploy/helm/agent-lib/templates/_values.tpl
PYPROJECT=$ROOT/packages/chassis/pyproject.toml
DRY_RUN=${PUBLISH_DRY_RUN:-0}
ZERO_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000

log() { printf '[publish-chassis] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

# x CMD...: run CMD, or print it under a dry run.
x() {
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

package_tag() {
  local version
  version=$(sed -n 's/^version = "\([0-9][0-9.]*\)"$/\1/p' "$PYPROJECT" | head -1)
  [[ -n $version ]] || die "no version in $PYPROJECT"
  printf 'v%s\n' "$version"
}

chart_tag() {
  sed -n 's/^    tag: \(v[0-9][0-9.]*\)$/\1/p' "$VALUES_TPL" | head -1
}

# check_digest VERB DIGEST: stop unless DIGEST is a real sha256 digest.
check_digest() {
  [[ $2 =~ ^sha256:[0-9a-f]{64}$ ]] || die "$1: '$2' is not sha256:<64 hex>"
  [[ $2 != "$ZERO_DIGEST" ]] || die "$1: the all-zero digest is the placeholder"
}

# pin DIGEST: replace the chart's one `    digest: sha256:<hex>` line and its placeholder note.
pin() {
  local digest=${1:-} tmp
  check_digest pin "$digest"
  [[ $(grep -cE '^    digest: sha256:[0-9a-f]{64}$' "$VALUES_TPL") == 1 ]] ||
    die "pin: $VALUES_TPL has no single chassis digest line"
  tmp=$(mktemp "$(dirname "$VALUES_TPL")/.values.XXXXXX")
  # The PLACEHOLDER note goes with the zero digest; the rest of the note stays.
  sed -E \
    -e "s|^    digest: sha256:[0-9a-f]{64}$|    digest: $digest|" \
    -e '/^    # PLACEHOLDER until the first push/d' \
    "$VALUES_TPL" >"$tmp"
  mv "$tmp" "$VALUES_TPL"
  log "pinned $IMAGE@$digest in ${VALUES_TPL#"$ROOT"/}"
}

# sign DIGEST: sign the pushed image by digest, never by tag, then pin it. Also the recovery
# when `publish` pushed and the signature failed.
sign() {
  local digest=${1:-}
  check_digest sign "$digest"
  # By digest, never by tag: the tag can move, the digest cannot.
  cosign sign --yes "$IMAGE@$digest"
  pin "$digest"
}

# tag_absent REF: true if the registry says REF is not found; false if REF exists; any other
# registry error (login, network, rate limit) stops the run: the check fails closed.
tag_absent() {
  local err
  if err=$(docker buildx imagetools inspect "$1" 2>&1 >/dev/null); then
    return 1
  fi
  if grep -qE 'not found|MANIFEST_UNKNOWN' <<<"$err"; then
    return 0
  fi
  die "cannot tell whether $1 is on the registry: $err"
}

# refuse REASON: stop, or under a dry run say what publish would refuse and go on.
refuse() {
  [[ $DRY_RUN == 1 ]] || die "$*"
  log "dry run: publish would refuse: $*"
}

publish() {
  local tag chart meta digest epoch
  tag=$(package_tag)
  chart=$(chart_tag)
  [[ $chart == "$tag" ]] ||
    die "the chart's chassis tag is ${chart:-missing}, the package is $tag: bump chassis.image.tag first"
  [[ -z $(git -C "$ROOT" status --porcelain) ]] ||
    refuse "the tree has changes; publish builds a clean commit only"
  git -C "$ROOT" merge-base --is-ancestor HEAD origin/main 2>/dev/null ||
    refuse "HEAD is not on origin/main; publish builds a merged commit only (git fetch origin first)"
  # A tag on the registry is never pushed again.
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ docker buildx imagetools inspect %s (refuse if it exists)\n' "$IMAGE:$tag"
  elif ! tag_absent "$IMAGE:$tag"; then
    die "$IMAGE:$tag is already on the registry; bump the version, never overwrite a tag"
  fi
  epoch=$(git -C "$ROOT" log -1 --format=%ct)
  meta=$(mktemp "${TMPDIR:-/tmp}/chassis-publish.XXXXXX")
  # shellcheck disable=SC2064 # expand now: $meta is local
  trap "rm -f '$meta'" EXIT
  log "building $IMAGE:$tag for $PLATFORMS from $(git -C "$ROOT" rev-parse --short HEAD)"
  x env "SOURCE_DATE_EPOCH=$epoch" docker buildx build --platform "$PLATFORMS" \
    -f packages/chassis/Dockerfile \
    --tag "$IMAGE:$tag" \
    --label "org.opencontainers.image.source=$SOURCE" \
    --label "org.opencontainers.image.version=$tag" \
    --label "org.opencontainers.image.revision=$(git -C "$ROOT" rev-parse HEAD)" \
    --sbom=true --provenance=mode=max \
    --push --metadata-file "$meta" .
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ cosign sign --yes %s@<the digest buildx writes to %s>\n' "$IMAGE" "$meta"
    printf '+ pin <that digest>\n'
    return
  fi
  digest=$(jq -r '."containerimage.digest" // empty' "$meta")
  [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || die "buildx wrote no digest to $meta"
  log "pushed $IMAGE:$tag@$digest; if signing fails, run: scripts/publish_chassis.sh sign $digest"
  sign "$digest"
}

cd "$ROOT"
cmd=${1:-help}
shift || true
case $cmd in
  plan) DRY_RUN=1 publish ;;
  publish) publish ;;
  pin | sign)
    VALUES_TPL=${CHASSIS_VALUES_TPL:-$VALUES_TPL}
    "$cmd" "$@"
    ;;
  *) sed -n '2,36p' "$0" ;;
esac
