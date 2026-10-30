{{/*
agent-lib.configmap: the chassis config, mounted read-only at /etc/chassis/config.yaml in the
chassis container only. It is chassis.config with two fields filled when they are empty:

- spec.engine.url: the workload on loopback (sidecar), or the workload's Service in the remote
  namespace (remote). The connector never follows the agent card's URL (ADR-005 decision 3).
- spec.engine.auth (remote lane): the bearer scheme with the token in REMOTE_TOKEN, which the
  chart puts in the chassis container from remote.tokenSecret.

No Secret value goes in here: the chassis reads its credentials from its env.
*/}}
{{- define "agent-lib.chassisConfig" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- $name := include "agent-lib.fullname" . -}}
{{- $cfg := deepCopy $v.chassis.config -}}
{{- $engine := $cfg.spec.engine -}}
{{- if not $engine.url -}}
{{- if eq $engine.connector "remote" -}}
{{- $ns := include "agent-lib.remoteNamespace" (dict "root" . "v" $v) -}}
{{- $_ := set $engine "url" (printf "http://%s-workload.%s.svc.cluster.local:%d" $name $ns (int $v.workload.port)) -}}
{{- else -}}
{{- $_ := set $engine "url" (printf "http://127.0.0.1:%d" (int $v.workload.port)) -}}
{{- end -}}
{{- end -}}
{{- if and (eq $engine.connector "remote") (not (hasKey $engine "auth")) -}}
{{- $_ := set $engine "auth" (dict "scheme" "bearer" "token_env" "REMOTE_TOKEN") -}}
{{- end -}}
{{- toYaml $cfg -}}
{{- end -}}

{{- define "agent-lib.configmap" -}}
{{- $name := include "agent-lib.fullname" . -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $name }}-chassis
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
data:
  config.yaml: |
    {{- include "agent-lib.chassisConfig" . | nindent 4 }}
{{- end -}}
