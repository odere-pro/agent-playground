{{- /*
One Argo CD Application per service (056 CH-7). The ring's overlay is the last value file, so its
ring.chassisTag holds the service on a tag, and the service's own chassis.image.tag (a pin) still
wins inside agent-lib. The sync wave orders the rings when a fast-ring urgent fix moves them all
in one sync (ADR-001 item 10): Argo CD waits for every Application in a wave to be healthy before
the next wave. Argo CD reports an Application's health only with the health check for
argoproj.io/Application in argocd-cm, which the platform adds; without it the waves do not wait.
*/ -}}
{{- $waves := dict "canary" "0" "ten-percent" "1" "all" "2" -}}
{{- if not .Values.repoURL -}}
{{- fail "agent-rings: repoURL is required, the repository Argo CD reads the charts from" -}}
{{- end -}}
{{- $seen := dict -}}
{{- $canaries := 0 -}}
{{- range $s := .Values.services -}}
{{- range $k := list "name" "path" "namespace" "ring" -}}
{{- if not (get $s $k) -}}
{{- fail (printf "agent-rings: service %v has no %s; each service needs name, path, namespace, and ring" (get $s "name") $k) -}}
{{- end -}}
{{- end -}}
{{- if not (hasKey $waves (toString $s.ring)) -}}
{{- fail (printf "agent-rings: service %s: ring %q must be canary, ten-percent, or all" $s.name (toString $s.ring)) -}}
{{- end -}}
{{- if hasKey $seen $s.name -}}
{{- fail (printf "agent-rings: service %s is named twice; a service is in one ring" $s.name) -}}
{{- end -}}
{{- $_ := set $seen $s.name true -}}
{{- if eq $s.ring "canary" -}}
{{- $canaries = add1 $canaries -}}
{{- end -}}
{{- end -}}
{{- if and .Values.services (ne (int $canaries) 1) -}}
{{- fail (printf "agent-rings: the plan needs exactly one canary service, not %d (suggested: one non-critical service; ADR-001 item 9)" (int $canaries)) -}}
{{- end -}}
{{- range $s := .Values.services }}
{{- $up := repeat (len (splitList "/" (trimSuffix "/" $s.path))) "../" }}
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {{ $s.name }}
  namespace: {{ $.Release.Namespace }}
  labels:
    app.kubernetes.io/part-of: agent-platform
    app.kubernetes.io/managed-by: {{ $.Release.Service }}
    agents.platform/chassis-ring: {{ $s.ring }}
  annotations:
    argocd.argoproj.io/sync-wave: {{ get $waves $s.ring | quote }}
spec:
  project: {{ $.Values.project }}
  source:
    repoURL: {{ $.Values.repoURL }}
    targetRevision: {{ $.Values.targetRevision }}
    path: {{ $s.path }}
    helm:
      valueFiles:
        - values.yaml
        - {{ printf "%s%s/%s.yaml" $up (trimSuffix "/" $.Values.ringsDir) $s.ring }}
  destination:
    server: {{ $.Values.destinationServer }}
    namespace: {{ $s.namespace }}
{{- end }}
