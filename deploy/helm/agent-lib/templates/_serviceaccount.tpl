{{/*
agent-lib.serviceaccount: one service account per pod, with no token mounted. The pod spec says
`automountServiceAccountToken: false` too, so neither the chassis nor the workload holds a
Kubernetes token (024 CH-3; the Kubernetes API is in the deny list).
*/}}
{{- define "agent-lib.serviceaccount" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- $lane := include "agent-lib.lane" $v -}}
{{- $name := include "agent-lib.fullname" . -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
automountServiceAccountToken: false
{{- if eq $lane "remote" }}
{{- $wname := printf "%s-workload" $name }}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $wname }}
  namespace: {{ include "agent-lib.remoteNamespace" (dict "root" . "v" $v) }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $wname) | nindent 4 }}
automountServiceAccountToken: false
{{- end }}
{{- end -}}
