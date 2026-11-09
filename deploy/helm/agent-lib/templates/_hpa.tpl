{{/*
agent-lib.hpa: optional, off by default (hpa.enabled). CPU utilization on the chassis pod, and
with hpa.runsInFlight.enabled (CH-15) the chassis's `chassis_runs_in_flight` gauge as a Pods
metric, averaged per pod: the chassis idles on CPU while it waits for the model, so in-flight
runs are the signal that scales it. A Pods metric needs a metrics adapter in the cluster that
serves the gauge (prometheus-adapter, suggested); the chart only declares it.
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
{{- if $v.hpa.runsInFlight.enabled }}
    - type: Pods
      pods:
        metric:
          name: chassis_runs_in_flight
        target:
          type: AverageValue
          averageValue: {{ int $v.hpa.runsInFlight.targetAverageValue | quote }}
{{- end }}
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
