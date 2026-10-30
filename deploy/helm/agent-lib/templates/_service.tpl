{{/*
agent-lib.service: the agent's Service. It selects the chassis pod and targets the chassis port
only. The workload port is never in it; in the sidecar lane the workload listens on 127.0.0.1.

remote lane, two more Services:
- <name>-remote-proxy, in front of the chassis's remote proxy port, on the fixed ClusterIP
  remote.proxyClusterIP (the remote pod has no DNS; PoC-5 used 10.96.85.91).
- <name>-workload in remote.namespace, so the chassis reaches its remote by name.
*/}}
{{- define "agent-lib.service" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- $lane := include "agent-lib.lane" $v -}}
{{- $name := include "agent-lib.fullname" . -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "agent-lib.selectorLabels" (dict "root" . "name" $name) | nindent 4 }}
  ports:
    - name: http
      port: {{ int $v.service.port }}
      targetPort: {{ int $v.chassis.port }}
      protocol: TCP
{{- if eq $lane "remote" }}
{{- $wname := printf "%s-workload" $name }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}-remote-proxy
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
spec:
  type: ClusterIP
  clusterIP: {{ $v.remote.proxyClusterIP }}
  selector:
    {{- include "agent-lib.selectorLabels" (dict "root" . "name" $name) | nindent 4 }}
  ports:
    - name: remote-proxy
      port: {{ int $v.chassis.remoteProxyPort }}
      targetPort: {{ int $v.chassis.remoteProxyPort }}
      protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $wname }}
  namespace: {{ include "agent-lib.remoteNamespace" (dict "root" . "v" $v) }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $wname) | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "agent-lib.selectorLabels" (dict "root" . "name" $wname) | nindent 4 }}
  ports:
    - name: a2a
      port: {{ int $v.workload.port }}
      targetPort: {{ int $v.workload.port }}
      protocol: TCP
{{- end }}
{{- end -}}
