# shellcheck shell=bash
# The NetworkPolicy apply split of the PoC-5 and PoC-6 kind scripts, in one place. Sourced, never
# run: deploy/kind/poc05/run.sh and deploy/kind/poc06/run.sh source it after lib.sh (which gives
# `kctl` and `die`) and after setting DEPLOYER, the platform deployer's identity.
: "${DEPLOYER:?set DEPLOYER in the script before sourcing policies.sh}"

# NetworkPolicies go in as cluster admin, everything else as the platform deployer (core review
# R5a-02; review F2). Neither side splits rendered text: the admin applies a policy file by path,
# and the deployer a kustomization that lists no policy file. Both are checked first with
# `apply --dry-run=client -o name`, which lists every object kubectl would send.
NETPOL_NAME='^networkpolicy\.networking\.k8s\.io/[a-z0-9.-]+$'

# apply_policies NS FILE: FILE as cluster admin, after checking it holds only NetworkPolicies.
# `-n NS` makes kubectl refuse an object that names another namespace, so a policy file can open
# an edge in NS only.
apply_policies() {
  local ns=$1 file=$2 names
  names=$(kctl apply --dry-run=client -o name -n "$ns" -f "$file") ||
    die "policies: $file does not apply in $ns"
  [[ -n $names ]] || die "policies: $file holds no object"
  if grep -qvE "$NETPOL_NAME" <<<"$names"; then
    die "policies: $file holds more than NetworkPolicies: $(grep -vE "$NETPOL_NAME" <<<"$names" |
      tr '\n' ' ')"
  fi
  kctl apply -n "$ns" -f "$file"
}

# apply_deployer: the manifest on stdin as the platform deployer, after checking it holds no
# NetworkPolicy (the deployer has no NetworkPolicy rights anyway, admission/rbac.yaml).
apply_deployer() {
  local manifest names
  manifest=$(cat)
  names=$(kctl apply --dry-run=client -o name -f - <<<"$manifest") ||
    die "deployer: the manifest does not render"
  if grep -qE '^networkpolicy\.' <<<"$names"; then
    die "deployer: the manifest holds a NetworkPolicy; it goes in by path, as cluster admin"
  fi
  kctl apply -f - --as="$DEPLOYER" <<<"$manifest"
}
