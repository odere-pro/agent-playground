{{/*
agent-lib values: the defaults, merged under the service chart's values, then checked.

Every top-level named template starts by including "agent-lib.values" and parsing it back with
fromYaml, so the checks in _validate.tpl run on every render, whichever template a chart includes.

The chassis image is set here, once, for every service that includes this chart (ADR-001 item 9).
A release bumps `chassis.image.tag` below; a service holds the previous tag with one value,
`chassis.image.tag`, in its own values. suggested: `poc09` until the chassis has release tags
(056 CH-7). The uids are not values: they come from the uid table in deploy/README.md.
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
    repository: kind.local/agent-platform/chassis
    tag: poc09
    # The chassis tags at or above the minimum version: the current tag and the previous one a
    # service may pin to roll back (ADR-001 item 9). Set once here, never by a service. The
    # render and admission rule 9 (agent-lib.admission) refuse any other chassis tag.
    # suggested: poc08 as the previous tag until the chassis has release tags (056 CH-7).
    supportedTags: [poc09, poc08]
    digest: ""
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
  tokenSecret:
    key: token
networkPolicy:
  ingressFrom: []
  chassisEgress: []
  dns:
    namespace: kube-system
    podLabels:
      k8s-app: kube-dns
hpa:
  enabled: false
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
{{- end -}}

{{- define "agent-lib.values" -}}
{{- $defaults := include "agent-lib.defaults" . | fromYaml -}}
{{- $user := .Values | toYaml | fromYaml -}}
{{- include "agent-lib.validateServiceValues" $user -}}
{{- $v := mergeOverwrite $defaults $user -}}
{{- include "agent-lib.validate" $v -}}
{{- toYaml $v -}}
{{- end -}}

{{/*
The remote workload's token Secret: one per remote, named after its pod's app.kubernetes.io/name
(ADR-005 decision 1; admission rule 7c holds the pod to it). It must exist in the chassis's
namespace and in remote.namespace. Takes the root.
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
