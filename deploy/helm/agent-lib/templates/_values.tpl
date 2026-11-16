{{/*
agent-lib values: the defaults, merged under the service chart's values, then checked.

Every top-level named template starts by including "agent-lib.values" and parsing it back with
fromYaml, so the checks in _validate.tpl run on every render, whichever template a chart includes.

The chassis image is set here, once, for every service that includes this chart (ADR-001 item 9).
It is the published image, ghcr.io/odere-pro/agent-chassis, tagged vX.Y.Z and pinned by digest
(139 CH-19, decided 2026-11-15). The tag is packages/chassis/pyproject.toml's version with a `v`.
A release bumps `chassis.image.tag` below and `scripts/publish_chassis.sh publish` writes its
digest. The digest pins this repository and tag only: a service whose pod runs another tag or
repository (its own pin, or its ring's hold) names its own `chassis.image.digest` too ("" pins by
tag), or the render fails (agent-lib.validateImagePin). The uids are not values: they come from
the uid table in deploy/README.md.

The rollout rings (056 CH-7): a service layers its ring's overlay, deploy/rings/<ring>.yaml, on
its own values. `ring.name` labels the chassis pod agents.platform/chassis-ring. `ring.chassisTag`
holds the ring on a tag while a release has not reached it; empty follows the library's tag. The
tag a pod runs is, first to last: the service's own `chassis.image.tag` (a pin), the ring's
`ring.chassisTag`, the library's tag. Each must be a supported tag.
*/}}
{{- define "agent-lib.defaults" -}}
nameOverride: ""
replicas: 1
# 50 = drain delay 10 + drain timeout 30 + 5 for pending events + 5 margin (ADR-012 item 3).
# The render refuses a grace below drainDelaySeconds + drainTimeoutSeconds + 5, and a delay or
# timeout that is negative or above the grace.
terminationGracePeriodSeconds: 50
chassis:
  image:
    repository: ghcr.io/odere-pro/agent-chassis
    # suggested: v0.1.0, the chassis package's version (packages/chassis/pyproject.toml); the
    # epic gives no first release number.
    tag: v0.1.0
    # The chassis tags at or above the minimum version: the current tag and the previous one a
    # service may pin to roll back (ADR-001 item 9). Set once here, never by a service. The
    # render and admission rule 9 (agent-lib.admission) refuse any other chassis tag.
    # suggested: poc09, the kind-era tag, as the previous one until a second release exists.
    supportedTags: [v0.1.0, poc09]
    # The digest of `tag` on ghcr.io, written by `scripts/publish_chassis.sh publish` (or `pin`).
    # On kind, values-kind.yaml sets the kind.local repository and "" here.
    # PLACEHOLDER until the first push: all zeros, so a pull fails; it never runs another image.
    digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
    pullPolicy: IfNotPresent
  port: 8080
  proxyPort: 8090
  remoteProxyPort: 8091
  startupWaitSeconds: 120
  drainDelaySeconds: 10
  drainTimeoutSeconds: 30
  extraArgs: []
  env: []
  secretEnv: []
  envFromSecrets: []
  secretVolumes: []
  config: {}
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 256Mi
  tmpSizeLimit: 16Mi
workload:
  image: ""
  pullPolicy: IfNotPresent
  port: 9000
  args: ["--drain-timeout-s", "30"]
  env: []
  probeCommand: []
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 256Mi
  tmpSizeLimit: 32Mi
service:
  port: 8080
remote:
  namespace: ""
  runtimeClassName: gvisor
  proxyClusterIP: ""
  terminationGracePeriodSeconds: 40
  # The Secret's name is not a value: <name>-workload-token (agent-lib.remoteTokenSecret).
  # It holds two keys, one per direction (055 CH-6), so a remote that replays the key the
  # chassis sends it cannot call the chassis's remote listener. suggested: the key names.
  tokenSecret:
    keys:
      # The chassis sends it (REMOTE_TOKEN); the workload requires it (CHASSIS_A2A_TOKEN).
      toWorkload: chassis-to-workload
      # The workload sends it (CHASSIS_API_TOKEN); the listener accepts it (REMOTE_PROXY_TOKEN).
      toChassis: workload-to-chassis
networkPolicy:
  ingressFrom: []
  chassisEgress: []
  dns:
    namespace: kube-system
    podLabels:
      k8s-app: kube-dns
# 056 CH-7: set by the ring overlay (deploy/rings/<ring>.yaml), not by the service's own values.
ring:
  name: ""
  chassisTag: ""
hpa:
  enabled: false
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
  # CH-15: scale on the chassis's `chassis_runs_in_flight` gauge too (a Pods metric, average
  # per pod; needs a metrics adapter that serves it, such as prometheus-adapter). suggested: 24,
  # three quarters of spec.limits.max_in_flight (32).
  runsInFlight:
    enabled: false
    targetAverageValue: 24
{{- end -}}

{{- define "agent-lib.values" -}}
{{- $defaults := include "agent-lib.defaults" . | fromYaml -}}
{{- $user := .Values | toYaml | fromYaml -}}
{{- include "agent-lib.validateServiceValues" $user -}}
{{- $library := include "agent-lib.defaults" . | fromYaml -}}
{{- $v := mergeOverwrite $defaults $user -}}
{{- include "agent-lib.ringTag" (dict "v" $v "user" $user) -}}
{{- include "agent-lib.validate" $v -}}
{{- include "agent-lib.validateImagePin" (dict "v" $v "user" $user "defaults" $library) -}}
{{- toYaml $v -}}
{{- end -}}

{{/*
The ring holds the chassis tag (056 CH-7) unless the service pins its own: a pin is a
`chassis.image.tag` in the service's values, so it is read before the defaults are merged. Sets
the merged chassis.image.tag in place; prints nothing. Takes (dict "v" <merged> "user" <raw>).
*/}}
{{- define "agent-lib.ringTag" -}}
{{- $held := toString .v.ring.chassisTag -}}
{{- $pinned := dig "chassis" "image" "tag" "" .user -}}
{{- if and $held (not $pinned) -}}
{{- $_ := set .v.chassis.image "tag" $held -}}
{{- end -}}
{{- end -}}

{{/*
The remote workload's token Secret: one per remote, named after its pod's app.kubernetes.io/name
(ADR-005 decision 1; admission rule 7c holds the pod to it). It holds two keys with different
values, remote.tokenSecret.keys.toWorkload and .toChassis (055 CH-6), and must exist in the
chassis's namespace and in remote.namespace. Made outside the chart. Takes the root.
*/}}
{{- define "agent-lib.remoteTokenSecret" -}}
{{- printf "%s-workload-token" (include "agent-lib.fullname" .) -}}
{{- end -}}

{{/* The lane and the trust level come from the chassis config, so the chart and the chassis read one value. */}}
{{- define "agent-lib.lane" -}}
{{- dig "spec" "engine" "connector" "" .chassis.config -}}
{{- end -}}

{{- define "agent-lib.trust" -}}
{{- dig "spec" "trust" "" .chassis.config -}}
{{- end -}}

{{- define "agent-lib.remoteNamespace" -}}
{{- default .root.Release.Namespace .v.remote.namespace -}}
{{- end -}}
