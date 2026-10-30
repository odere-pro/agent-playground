{{/*
Names, labels, the chassis image, and the security context shared by every container.
*/}}

{{/* The release's base name. 50 characters, so "<name>-remote-proxy" stays a valid Service name. */}}
{{- define "agent-lib.fullname" -}}
{{- default .Release.Name .Values.nameOverride | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{/* Selector labels. Takes (dict "root" $ "name" <object base name>). */}}
{{- define "agent-lib.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{/* Object labels. Takes (dict "root" $ "name" <object base name>). */}}
{{- define "agent-lib.labels" -}}
{{ include "agent-lib.selectorLabels" . }}
app.kubernetes.io/part-of: agent-platform
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{/* The chassis image reference: repository:tag, or repository[:tag]@digest when a digest is set. */}}
{{- define "agent-lib.chassisImage" -}}
{{- if .digest -}}
{{- if .tag -}}
{{- printf "%s:%s@%s" .repository (toString .tag) .digest -}}
{{- else -}}
{{- printf "%s@%s" .repository .digest -}}
{{- end -}}
{{- else -}}
{{- printf "%s:%s" .repository (toString .tag) -}}
{{- end -}}
{{- end -}}

{{/*
The container security context. Takes the uid from the uid table in deploy/README.md:
chassis 10001, every workload 10002. The gid equals the uid.
*/}}
{{- define "agent-lib.containerSecurityContext" -}}
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* The pod security context, the same for every pod the chart renders. */}}
{{- define "agent-lib.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* A NetworkPolicy peer: a pod by its selector labels in a named namespace. Takes (dict "root" $ "name" <base name> "namespace" <ns>). */}}
{{- define "agent-lib.peer" -}}
- namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: {{ .namespace }}
  podSelector:
    matchLabels:
      {{- include "agent-lib.selectorLabels" . | nindent 6 }}
{{- end -}}
