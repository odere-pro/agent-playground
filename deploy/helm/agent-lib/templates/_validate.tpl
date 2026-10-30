{{/*
agent-lib.validate: refuse values that would break the boundary. Called by agent-lib.values with
the merged values; prints nothing; fails the render with an "agent-lib:" message.

- The workload takes an allow-list of keys. There is no key for a Secret, an envFrom, a volume,
  or a security context, and an unknown key fails, so values cannot give the workload a Secret
  (ADR-001 hard requirement 1; PoC-5 admission rule 6c). Its env is plain name/value pairs, never
  a credential-looking name, never a name the chart sets itself.
- The chassis gets Secrets only through chassis.secretEnv, chassis.envFromSecrets, and
  chassis.secretVolumes. No `command` (admission rule 6b).
- The trust rule: spec.trust untrusted needs spec.engine.connector remote (ADR-005 decision 4).
- Images pinned: the chassis by tag or digest, never latest; the workload by tag or digest.
- The remote lane: one replica (the remote listener answers only for a run in this process), no
  HPA, a token Secret, a fixed ClusterIP for the remote proxy (the remote pod has no DNS), gVisor.
- Egress rules name a peer by label; no ipBlock and no rule without a `to` (deploy/CLAUDE.md).
*/}}
{{- define "agent-lib.validate" -}}
{{- $v := . -}}
{{- $credential := "(?i)(^|_)(API_?KEY|KEY|TOKEN|PGPASSWORD|PASSWORD|PASSWD|SECRET|CREDENTIALS?)($|_)" -}}

{{- /* Arguments: an allow-list. A flag here can move a listener off loopback or turn auth off,
and argparse accepts abbreviations, so a deny-list cannot hold. */ -}}
{{- $safeArg := "^(--log-level(=[a-z]+)?|debug|info|warning|error)$" -}}
{{- range concat ($v.workload.args | default list) (dig "extraArgs" (list) $v.chassis) -}}
{{- if not (regexMatch $safeArg (toString .)) -}}
{{- fail (printf "agent-lib: argument %q is not allowed: only --log-level" (toString .)) -}}
{{- end -}}
{{- end -}}

{{- /* The workload. */ -}}
{{- $workloadKeys := list "image" "pullPolicy" "port" "args" "env" "probeCommand" "resources" "tmpSizeLimit" -}}
{{- range $k, $_ := $v.workload -}}
{{- if not (has $k $workloadKeys) -}}
{{- fail (printf "agent-lib: workload.%s is not a workload value. The workload gets no Secret, envFrom, volume, or security context; only the chassis holds credentials (ADR-001 hard requirement 1). Allowed: %s" $k (join ", " $workloadKeys)) -}}
{{- end -}}
{{- end -}}
{{- $reserved := list "HOST" "PORT" "POD_IP" "CHASSIS_MODEL_URL" "CHASSIS_TOOL_URL" "CHASSIS_API_TOKEN" -}}
{{- range $i, $e := $v.workload.env -}}
{{- if not (and (kindIs "map" $e) (kindIs "string" $e.name)) -}}
{{- fail (printf "agent-lib: workload.env[%d] needs a name and a value" $i) -}}
{{- end -}}
{{- range $k, $_ := $e -}}
{{- if not (has $k (list "name" "value")) -}}
{{- fail (printf "agent-lib: workload.env %s has %s; the workload takes plain name/value pairs only, and a Secret goes to chassis.secretEnv" $e.name $k) -}}
{{- end -}}
{{- end -}}
{{- if regexMatch $credential $e.name -}}
{{- fail (printf "agent-lib: workload.env %s looks like a credential; the workload holds none, and no values file holds a secret value" $e.name) -}}
{{- end -}}
{{- if has $e.name $reserved -}}
{{- fail (printf "agent-lib: workload.env %s is set by the chart; reserved: %s" $e.name (join ", " $reserved)) -}}
{{- end -}}
{{- end -}}
{{- if not $v.workload.image -}}
{{- fail "agent-lib: workload.image is required" -}}
{{- end -}}
{{- if not (regexMatch "(:[^/:@]+|@sha256:[a-f0-9]{64})$" $v.workload.image) -}}
{{- fail (printf "agent-lib: workload.image %s needs a tag or a digest" $v.workload.image) -}}
{{- end -}}
{{- if hasSuffix ":latest" $v.workload.image -}}
{{- fail (printf "agent-lib: workload.image %s: pin a tag or a digest, never latest" $v.workload.image) -}}
{{- end -}}

{{- /* The chassis. */ -}}
{{- $chassisKeys := list "image" "port" "proxyPort" "remoteProxyPort" "startupWaitSeconds" "drainDelaySeconds" "drainTimeoutSeconds" "extraArgs" "env" "secretEnv" "envFromSecrets" "secretVolumes" "config" "resources" "tmpSizeLimit" -}}
{{- range $k, $_ := $v.chassis -}}
{{- if not (has $k $chassisKeys) -}}
{{- fail (printf "agent-lib: chassis.%s is not a chassis value (no command: admission rule 6b). Allowed: %s" $k (join ", " $chassisKeys)) -}}
{{- end -}}
{{- end -}}
{{- $chassisReserved := list "POD_IP" "REMOTE_TOKEN" -}}
{{- range $i, $e := $v.chassis.env -}}
{{- if not (and (kindIs "map" $e) (kindIs "string" $e.name)) -}}
{{- fail (printf "agent-lib: chassis.env[%d] needs a name and a value" $i) -}}
{{- end -}}
{{- range $k, $_ := $e -}}
{{- if not (has $k (list "name" "value")) -}}
{{- fail (printf "agent-lib: chassis.env %s has %s; plain name/value pairs only, a Secret goes to chassis.secretEnv" $e.name $k) -}}
{{- end -}}
{{- end -}}
{{- if regexMatch $credential $e.name -}}
{{- fail (printf "agent-lib: chassis.env %s looks like a credential; reference a Secret in chassis.secretEnv, never a value in a values file" $e.name) -}}
{{- end -}}
{{- if has $e.name $chassisReserved -}}
{{- fail (printf "agent-lib: chassis.env %s is set by the chart" $e.name) -}}
{{- end -}}
{{- end -}}
{{- range $i, $e := $v.chassis.secretEnv -}}
{{- if not (and (kindIs "map" $e) $e.name $e.secretName $e.key) -}}
{{- fail (printf "agent-lib: chassis.secretEnv[%d] needs name, secretName, and key" $i) -}}
{{- end -}}
{{- if has $e.name $chassisReserved -}}
{{- fail (printf "agent-lib: chassis.secretEnv %s is set by the chart" $e.name) -}}
{{- end -}}
{{- end -}}
{{- range $i, $e := $v.chassis.secretVolumes -}}
{{- if not (and (kindIs "map" $e) $e.name $e.secretName $e.mountPath) -}}
{{- fail (printf "agent-lib: chassis.secretVolumes[%d] needs name, secretName, and mountPath" $i) -}}
{{- end -}}
{{- if or (hasPrefix "/tmp" $e.mountPath) (hasPrefix "/etc/chassis" $e.mountPath) -}}
{{- fail (printf "agent-lib: chassis.secretVolumes %s: mountPath %s is taken by the chart" $e.name $e.mountPath) -}}
{{- end -}}
{{- end -}}
{{- $img := $v.chassis.image -}}
{{- if eq (toString $img.tag) "latest" -}}
{{- fail "agent-lib: chassis.image.tag latest is not a pin; set a tag or a digest" -}}
{{- end -}}
{{- if and (not $img.digest) (not $img.tag) -}}
{{- fail "agent-lib: chassis.image needs a tag or a digest" -}}
{{- end -}}
{{- if and $img.digest (not (regexMatch "^sha256:[a-f0-9]{64}$" $img.digest)) -}}
{{- fail (printf "agent-lib: chassis.image.digest %s is not sha256:<64 hex>" $img.digest) -}}
{{- end -}}
{{- if not (kindIs "map" $v.chassis.config) -}}
{{- fail "agent-lib: chassis.config must be the chassis config document (a map)" -}}
{{- end -}}

{{- /* The lane and the trust rule. */ -}}
{{- $lane := include "agent-lib.lane" $v -}}
{{- $trust := include "agent-lib.trust" $v -}}
{{- if not (has $lane (list "sidecar" "remote")) -}}
{{- fail (printf "agent-lib: chassis.config.spec.engine.connector must be sidecar or remote, got %q; the chart does not run the inprocess lane" $lane) -}}
{{- end -}}
{{- if not (has $trust (list "trusted" "untrusted")) -}}
{{- fail (printf "agent-lib: chassis.config.spec.trust must be trusted or untrusted, got %q (a pod with no trust label is refused at admission)" $trust) -}}
{{- end -}}
{{- if and (eq $trust "untrusted") (ne $lane "remote") -}}
{{- fail "agent-lib: spec.trust untrusted needs spec.engine.connector remote (the trust rule, ADR-005 decision 4)" -}}
{{- end -}}

{{- /* The remote lane. */ -}}
{{- if eq $lane "remote" -}}
{{- if ne (int $v.replicas) 1 -}}
{{- fail "agent-lib: the remote lane runs one replica: the remote listener answers only for a run in flight in its own process (run_required)" -}}
{{- end -}}
{{- if $v.hpa.enabled -}}
{{- fail "agent-lib: hpa.enabled in the remote lane; it runs one replica (run_required)" -}}
{{- end -}}
{{- if not $v.remote.tokenSecret.name -}}
{{- fail "agent-lib: the remote lane needs remote.tokenSecret.name, the Secret with the per-remote bearer token (ADR-005 decision 1)" -}}
{{- end -}}
{{- if not $v.remote.tokenSecret.key -}}
{{- fail "agent-lib: remote.tokenSecret.key is empty" -}}
{{- end -}}
{{- if not (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}$" (toString $v.remote.proxyClusterIP)) -}}
{{- fail "agent-lib: the remote lane needs remote.proxyClusterIP, a fixed IPv4 ClusterIP inside the cluster's Service range: the remote pod has no DNS" -}}
{{- end -}}
{{- if ne (toString $v.remote.runtimeClassName) "gvisor" -}}
{{- fail "agent-lib: remote.runtimeClassName must be gvisor; the remote lane runs under gVisor" -}}
{{- end -}}
{{- if not $v.remote.namespace -}}
{{- fail "agent-lib: the remote lane needs remote.namespace, apart from the chassis's" -}}
{{- end -}}
{{- $auth := dig "spec" "engine" "auth" (dict) $v.chassis.config -}}
{{- if and $auth.token_env (ne $auth.token_env "REMOTE_TOKEN") -}}
{{- fail (printf "agent-lib: spec.engine.auth.token_env %s: the chart puts the remote token in REMOTE_TOKEN" $auth.token_env) -}}
{{- end -}}
{{- end -}}

{{- /* The HPA. */ -}}
{{- if $v.hpa.enabled -}}
{{- if or (lt (int $v.hpa.minReplicas) 1) (lt (int $v.hpa.maxReplicas) (int $v.hpa.minReplicas)) -}}
{{- fail "agent-lib: hpa needs 1 <= minReplicas <= maxReplicas" -}}
{{- end -}}
{{- end -}}

{{- /* Egress: peers by label only. */ -}}
{{- range $i, $rule := $v.networkPolicy.chassisEgress -}}
{{- if not (and (kindIs "map" $rule) $rule.to) -}}
{{- fail (printf "agent-lib: networkPolicy.chassisEgress[%d] has no `to`, which would allow every destination" $i) -}}
{{- end -}}
{{- range $peer := $rule.to -}}
{{- if hasKey $peer "ipBlock" -}}
{{- fail (printf "agent-lib: networkPolicy.chassisEgress[%d] uses an ipBlock; name the peer by namespace and pod labels (deploy/CLAUDE.md, default-deny egress)" $i) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $i, $peer := $v.networkPolicy.ingressFrom -}}
{{- if hasKey $peer "ipBlock" -}}
{{- fail (printf "agent-lib: networkPolicy.ingressFrom[%d] uses an ipBlock; name the peer by labels" $i) -}}
{{- end -}}
{{- end -}}
{{- end -}}
