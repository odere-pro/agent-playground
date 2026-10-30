{{/*
agent-lib.networkpolicy: what each pod may send and receive. Each policy names both directions,
so everything it does not allow is refused. The namespace should carry its own default deny too
(deploy/kind/poc05/base/default-deny.yaml).

The chassis pod: in, the chassis port only (from networkPolicy.ingressFrom, or any pod when it is
empty, as PoC-5); out, DNS plus networkPolicy.chassisEgress (peers by label: no ipBlock, no rule
without a `to`). The workload port is never allowed in. In the sidecar lane a policy cannot tell
the two containers apart (one network namespace), so the workload rides the chassis's egress and
the services' own auth is the control (PoC-5, agents/network-policy.yaml).

remote lane, added:
- the chassis pod: in, the remote proxy port from its own workload pod only; out, the workload's
  A2A port.
- the workload pod: in, its A2A port from its own chassis pod only; out, the chassis's remote
  proxy port and nothing else. No DNS rule: a remote that asks for a name gets no answer (H20).
*/}}
{{- define "agent-lib.networkpolicy" -}}
{{- $v := include "agent-lib.values" . | fromYaml -}}
{{- $lane := include "agent-lib.lane" $v -}}
{{- $name := include "agent-lib.fullname" . -}}
{{- $wname := printf "%s-workload" $name -}}
{{- $rns := include "agent-lib.remoteNamespace" (dict "root" . "v" $v) -}}
{{- $np := $v.networkPolicy -}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $name) | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "agent-lib.selectorLabels" (dict "root" . "name" $name) | nindent 6 }}
  policyTypes: [Ingress, Egress]
  ingress:
    - ports:
        - port: {{ int $v.chassis.port }}
          protocol: TCP
      {{- with $np.ingressFrom }}
      from:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    {{- if eq $lane "remote" }}
    - from:
        {{- include "agent-lib.peer" (dict "root" . "name" $wname "namespace" $rns) | nindent 8 }}
      ports:
        - port: {{ int $v.chassis.remoteProxyPort }}
          protocol: TCP
    {{- end }}
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $np.dns.namespace }}
          podSelector:
            matchLabels:
              {{- toYaml $np.dns.podLabels | nindent 14 }}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    {{- with $np.chassisEgress }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
    {{- if eq $lane "remote" }}
    - to:
        {{- include "agent-lib.peer" (dict "root" . "name" $wname "namespace" $rns) | nindent 8 }}
      ports:
        - port: {{ int $v.workload.port }}
          protocol: TCP
    {{- end }}
{{- if eq $lane "remote" }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $wname }}
  namespace: {{ $rns }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" $wname) | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "agent-lib.selectorLabels" (dict "root" . "name" $wname) | nindent 6 }}
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        {{- include "agent-lib.peer" (dict "root" . "name" $name "namespace" .Release.Namespace) | nindent 8 }}
      ports:
        - port: {{ int $v.workload.port }}
          protocol: TCP
  # The policy sees the pod after the Service rewrite (PoC-5 spike, question 2), so the fixed
  # ClusterIP of <name>-remote-proxy is covered by this pod peer.
  egress:
    - to:
        {{- include "agent-lib.peer" (dict "root" . "name" $name "namespace" .Release.Namespace) | nindent 8 }}
      ports:
        - port: {{ int $v.chassis.remoteProxyPort }}
          protocol: TCP
{{- end }}
{{- end -}}
