{{/*
agent-lib.deployment: the agent's pod.

sidecar lane: one pod, two containers. The chassis is the native sidecar (an init container with
`restartPolicy: Always`) and the workload the main container, as on kind in PoC-5
(deploy/kind/poc05/agents/agent-echo.yaml): the chassis must bind its proxies before the workload
starts (contract v4, "Bind order"). PoC-4 measured the reverse order for drain; 024 CH-3 leaves
the pick to the owner, and this chart carries PoC-5's.

remote lane (ADR-005, deploy/kind/poc05/): two pods. The chassis pod runs the chassis alone,
outside the sandbox, with every internal credential. The workload pod runs under the gVisor
RuntimeClass in remote.namespace with its own token only. A plain Deployment, not an
agent-sandbox Sandbox: the pod spec is the one PoC-5's Sandbox carried.

Both: no service account token, no Service links, a read-only root, a memory-backed /tmp per
container with a size cap (H22), and the uid table's uids.
*/}}
{{- define "agent-lib.deployment" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- $lane := include "agent-lib.lane" $v -}}
{{- $trust := include "agent-lib.trust" $v -}}
{{- $name := include "agent-lib.fullname" . -}}
{{- $args := dict "root" . "v" $v "lane" $lane -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
spec:
  {{- if not $v.hpa.enabled }}
  replicas: {{ int $v.replicas }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "agent-lib.selectorLabels" (dict "root" . "name" $name) | nindent 6 }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  template:
    metadata:
      labels:
        {{- include "agent-lib.selectorLabels" (dict "root" . "name" $name) | nindent 8 }}
        agents.platform/role: chassis
        agents.platform/lane: {{ $lane }}
        agents.platform/trust: {{ $trust }}
      annotations:
        checksum/chassis-config: {{ include "agent-lib.chassisConfig" . | sha256sum | quote }}
    spec:
      serviceAccountName: {{ $name }}
      automountServiceAccountToken: false
      enableServiceLinks: false
      terminationGracePeriodSeconds: {{ int $v.terminationGracePeriodSeconds }}
      securityContext:
        {{- include "agent-lib.podSecurityContext" . | nindent 8 }}
      {{- if eq $lane "sidecar" }}
      initContainers:
        {{- include "agent-lib.chassisContainer" $args | nindent 8 }}
      containers:
        {{- include "agent-lib.workloadContainer" $args | nindent 8 }}
      {{- else }}
      containers:
        {{- include "agent-lib.chassisContainer" $args | nindent 8 }}
      {{- end }}
      volumes:
        # One /tmp per container, memory-backed and capped: a shared writable volume would be a
        # side channel, and a write over the cap fails at the cap (H22).
        - name: chassis-tmp
          emptyDir:
            medium: Memory
            sizeLimit: {{ $v.chassis.tmpSizeLimit }}
        - name: chassis-config
          configMap:
            name: {{ $name }}-chassis
        {{- if eq $lane "sidecar" }}
        - name: workload-tmp
          emptyDir:
            medium: Memory
            sizeLimit: {{ $v.workload.tmpSizeLimit }}
        {{- end }}
        {{- range $v.chassis.secretVolumes }}
        - name: {{ printf "secret-%s" .name | quote }}
          secret:
            secretName: {{ .secretName | quote }}
        {{- end }}
{{- if eq $lane "remote" }}
{{- $wname := printf "%s-workload" $name }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $wname }}
  namespace: {{ include "agent-lib.remoteNamespace" (dict "root" . "v" $v) }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $wname) | nindent 4 }}
spec:
  # One replica, as its chassis (run_required).
  replicas: 1
  selector:
    matchLabels:
      {{- include "agent-lib.selectorLabels" (dict "root" . "name" $wname) | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "agent-lib.selectorLabels" (dict "root" . "name" $wname) | nindent 8 }}
        agents.platform/lane: remote
        agents.platform/trust: {{ $trust }}
    spec:
      serviceAccountName: {{ $wname }}
      automountServiceAccountToken: false
      enableServiceLinks: false
      runtimeClassName: {{ $v.remote.runtimeClassName }}
      terminationGracePeriodSeconds: {{ int $v.remote.terminationGracePeriodSeconds }}
      securityContext:
        {{- include "agent-lib.podSecurityContext" . | nindent 8 }}
      containers:
        {{- include "agent-lib.remoteWorkloadContainer" $args | nindent 8 }}
      volumes:
        # gVisor gives the sandbox its own /tmp even with a read-only root; this one is mounted
        # anyway so the cap holds (H22).
        - name: workload-tmp
          emptyDir:
            medium: Memory
            sizeLimit: {{ $v.workload.tmpSizeLimit }}
{{- end }}
{{- end -}}
