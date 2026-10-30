{{/*
agent-lib.hpa: optional, off by default (hpa.enabled). CPU utilization on the chassis pod.
Refused in the remote lane, which runs one replica (run_required). With the HPA on, the
Deployment leaves `replicas` to it. suggested: 70 % of the CPU request; right-sizing is 122 X-5.
*/}}
{{- define "agent-lib.hpa" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- if $v.hpa.enabled -}}
{{- $name := include "agent-lib.fullname" . -}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ $name }}
  minReplicas: {{ int $v.hpa.minReplicas }}
  maxReplicas: {{ int $v.hpa.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ int $v.hpa.targetCPUUtilizationPercentage }}
{{- end -}}
{{- end -}}

{{/* agent-lib.all: every object, for a service chart that wants one line. */}}
{{- define "agent-lib.all" -}}
{{ include "agent-lib.serviceaccount" . }}
---
{{ include "agent-lib.configmap" . }}
---
{{ include "agent-lib.deployment" . }}
---
{{ include "agent-lib.service" . }}
---
{{ include "agent-lib.networkpolicy" . }}
{{- with include "agent-lib.hpa" . }}
---
{{ . }}
{{- end }}
{{- end -}}
