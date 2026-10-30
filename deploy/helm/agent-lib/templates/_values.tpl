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
# 50 = drain delay 10 + drain timeout 30 + 10 margin (024 CH-3, PoC-4 status).
terminationGracePeriodSeconds: 50
chassis:
  image:
    repository: kind.local/agent-platform/chassis
    tag: poc09
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
  tokenSecret:
    name: ""
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
{{- $v := mergeOverwrite $defaults $user -}}
{{- include "agent-lib.validate" $v -}}
{{- toYaml $v -}}
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
