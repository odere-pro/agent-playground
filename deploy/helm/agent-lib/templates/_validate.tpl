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
- Images pinned: the chassis by a tag from the library's supported list, with or without a digest
  next to it, never latest (ADR-001 item 9; admission rule 9); the workload by tag or digest.
- The remote lane: one replica (the remote listener answers only for a run in this process), no
  HPA, a fixed ClusterIP for the remote proxy (the remote pod has no DNS), gVisor. The token
  Secret is named by the chart (admission rule 7c), not by a value, and holds two different
  keys, one per direction; one key both ways (remote.tokenSecret.key) is refused (055 CH-6).
- Egress rules name a peer by namespace and pod labels and a port; no ipBlock, no rule without a
  `to`, no empty selector (deploy/CLAUDE.md; 026 CH-4: default-deny egress).
- The service's raw values (agent-lib.validateServiceValues): a key the chart does not read is
  refused, so no value can look like it switches a rule off; the admission rules are the
  platform's (agent-lib.admission), the supported chassis tags the library's.
*/}}
{{- define "agent-lib.validateServiceValues" -}}
{{- $keys := list "nameOverride" "replicas" "terminationGracePeriodSeconds" "chassis" "workload" "service" "remote" "networkPolicy" "hpa" -}}
{{- range $k, $_ := . -}}
{{- if eq $k "admission" -}}
{{- fail "agent-lib: admission is not a service value. The admission rules are the platform's (agent-lib.admission, deploy/helm/agent-admission) and bind to the namespace, so a service cannot switch them off" -}}
{{- end -}}
{{- if not (or (has $k $keys) (has $k (list "global" "agent-lib"))) -}}
{{- fail (printf "agent-lib: %s is not a value the chart reads. Allowed: %s" $k (join ", " $keys)) -}}
{{- end -}}
{{- end -}}
{{- if hasKey (dig "chassis" "image" (dict) .) "supportedTags" -}}
{{- fail "agent-lib: chassis.image.supportedTags is set once, in the library chart (ADR-001 item 9), never by a service; pin chassis.image.tag to one of them" -}}
{{- end -}}
{{- if dig "remote" "tokenSecret" "name" "" . -}}
{{- fail "agent-lib: remote.tokenSecret.name is not a value: the chart names the remote's one Secret after its pod, <name>-workload-token (ADR-005 decision 1; admission rule 7c)" -}}
{{- end -}}
{{- if hasKey (dig "remote" "tokenSecret" (dict) .) "key" -}}
{{- fail "agent-lib: remote.tokenSecret.key is one key both ways, which is refused: the workload token Secret holds one key per direction, remote.tokenSecret.keys.toWorkload and .toChassis (055 CH-6)" -}}
{{- end -}}
{{- end -}}

{{/* A NetworkPolicy peer named by labels: no ipBlock, a namespace and a pod, neither empty. Takes (dict "peer" <peer> "where" <values path>). */}}
{{- define "agent-lib.validatePeer" -}}
{{- $p := .peer -}}
{{- if hasKey $p "ipBlock" -}}
{{- fail (printf "agent-lib: %s uses an ipBlock; name the peer by namespace and pod labels (deploy/CLAUDE.md, default-deny egress)" .where) -}}
{{- end -}}
{{- if not (dig "namespaceSelector" "matchLabels" (dict) $p) -}}
{{- fail (printf "agent-lib: %s names no namespace: a peer needs namespaceSelector.matchLabels (an empty selector is every namespace)" .where) -}}
{{- end -}}
{{- if not (dig "podSelector" "matchLabels" (dict) $p) -}}
{{- fail (printf "agent-lib: %s names no pod: a peer needs podSelector.matchLabels (an empty selector is every pod)" .where) -}}
{{- end -}}
{{- end -}}
{{- define "agent-lib.validate" -}}
{{- $v := . -}}
{{- $credential := "(?i)(^|_)(API_?KEY|KEY|TOKEN|PGPASSWORD|PASSWORD|PASSWD|SECRET|CREDENTIALS?)($|_)" -}}

{{- /* Arguments: an allow-list. A flag here can move a listener off loopback or turn auth off,
and argparse accepts abbreviations, so a deny-list cannot hold. */ -}}
{{- $safeArg := "^(--log-level(=[a-z]+)?|debug|info|warning|error|--drain-(delay|timeout)-s(=[0-9]{1,4})?|[0-9]{1,4})$" -}}
{{- range concat ($v.workload.args | default list) (dig "extraArgs" (list) $v.chassis) -}}
{{- if not (regexMatch $safeArg (toString .)) -}}
{{- fail (printf "agent-lib: argument %q is not allowed: only --log-level and --drain-delay-s or --drain-timeout-s" (toString .)) -}}
{{- end -}}
{{- end -}}

{{- /* The workload. */ -}}
{{- $workloadKeys := list "image" "pullPolicy" "port" "args" "env" "probeCommand" "resources" "tmpSizeLimit" -}}
{{- range $k, $_ := $v.workload -}}
{{- if not (has $k $workloadKeys) -}}
{{- fail (printf "agent-lib: workload.%s is not a workload value. The workload gets no Secret, envFrom, volume, or security context; only the chassis holds credentials (ADR-001 hard requirement 1). Allowed: %s" $k (join ", " $workloadKeys)) -}}
{{- end -}}
{{- end -}}
{{- $reserved := list "HOST" "PORT" "POD_IP" "CHASSIS_MODEL_URL" "CHASSIS_TOOL_URL" "CHASSIS_API_TOKEN" "CHASSIS_A2A_TOKEN" -}}
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
{{- $chassisReserved := list "POD_IP" "REMOTE_TOKEN" "REMOTE_PROXY_TOKEN" -}}
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
{{- fail "agent-lib: chassis.image.tag latest is not a pin; set a supported tag, with a digest next to it if you pin by digest" -}}
{{- end -}}
{{- if not $img.tag -}}
{{- fail "agent-lib: chassis.image.tag is required, with or without a digest next to it: admission rule 9 reads the chassis tag, and a chassis pinned by digest alone has none" -}}
{{- end -}}
{{- if not (has (toString $img.tag) $img.supportedTags) -}}
{{- fail (printf "agent-lib: chassis.image.tag %s is below the minimum chassis version; the library chart supports %s (ADR-001 item 9; admission rule 9 refuses it too)" (toString $img.tag) (join ", " $img.supportedTags)) -}}
{{- end -}}
{{- if and $img.digest (not (regexMatch "^sha256:[a-f0-9]{64}$" $img.digest)) -}}
{{- fail (printf "agent-lib: chassis.image.digest %s is not sha256:<64 hex>" $img.digest) -}}
{{- end -}}
{{- if not (kindIs "map" $v.chassis.config) -}}
{{- fail "agent-lib: chassis.config must be the chassis config document (a map)" -}}
{{- end -}}

{{- /* The pod's grace covers the chassis's worst-case drain (ADR-012 item 3): the drain delay,
the drain timeout, and 5 s for pending result events. A drain flag in chassis.extraArgs comes after
the chart's own on the command line, so it is the one the chassis reads; it is counted here. */ -}}
{{- $delay := int $v.chassis.drainDelaySeconds -}}
{{- $timeout := int $v.chassis.drainTimeoutSeconds -}}
{{- $prev := "" -}}
{{- range (dig "extraArgs" (list) $v.chassis) -}}
{{- $a := toString . -}}
{{- if eq $prev "--drain-delay-s" -}}{{- $delay = atoi $a -}}{{- end -}}
{{- if eq $prev "--drain-timeout-s" -}}{{- $timeout = atoi $a -}}{{- end -}}
{{- if hasPrefix "--drain-delay-s=" $a -}}{{- $delay = atoi (trimPrefix "--drain-delay-s=" $a) -}}{{- end -}}
{{- if hasPrefix "--drain-timeout-s=" $a -}}{{- $timeout = atoi (trimPrefix "--drain-timeout-s=" $a) -}}{{- end -}}
{{- $prev = $a -}}
{{- end -}}
{{- $grace := float64 $v.terminationGracePeriodSeconds -}}
{{- /* Each term on its own first, as a float: a huge value would clamp in atoi or wrap in add. */ -}}
{{- range $key, $value := dict "chassis.drainDelaySeconds" (list (float64 $v.chassis.drainDelaySeconds) $delay) "chassis.drainTimeoutSeconds" (list (float64 $v.chassis.drainTimeoutSeconds) $timeout) -}}
{{- range $value -}}
{{- if or (lt (float64 .) 0.0) (gt (float64 .) $grace) -}}
{{- fail (printf "agent-lib: %s %v must be 0 or more and at most terminationGracePeriodSeconds %v (ADR-012 item 3; a drain flag in chassis.extraArgs counts)" $key . $v.terminationGracePeriodSeconds) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $bound := add $delay $timeout 5 -}}
{{- if lt (int $v.terminationGracePeriodSeconds) (int $bound) -}}
{{- fail (printf "agent-lib: terminationGracePeriodSeconds %d is below chassis.drainDelaySeconds (%d) + chassis.drainTimeoutSeconds (%d) + 5 s for pending events = %d; the kubelet would kill the chassis mid-drain (ADR-012 item 3; a drain flag in chassis.extraArgs counts)" (int $v.terminationGracePeriodSeconds) $delay $timeout (int $bound)) -}}
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
{{- $keys := dig "tokenSecret" "keys" (dict) $v.remote -}}
{{- if not (and $keys.toWorkload $keys.toChassis) -}}
{{- fail "agent-lib: remote.tokenSecret.keys needs toWorkload and toChassis, one key per direction (055 CH-6)" -}}
{{- end -}}
{{- if eq (toString $keys.toWorkload) (toString $keys.toChassis) -}}
{{- fail "agent-lib: remote.tokenSecret.keys.toWorkload and .toChassis are the same key; the workload token Secret holds one key per direction (055 CH-6)" -}}
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
{{- if and $auth.proxy_token_env (ne $auth.proxy_token_env "REMOTE_PROXY_TOKEN") -}}
{{- fail (printf "agent-lib: spec.engine.auth.proxy_token_env %s: the chart puts the listener's own key in REMOTE_PROXY_TOKEN, one key per direction (055 CH-6)" $auth.proxy_token_env) -}}
{{- end -}}
{{- end -}}

{{- /* The HPA. */ -}}
{{- if $v.hpa.enabled -}}
{{- if or (lt (int $v.hpa.minReplicas) 1) (lt (int $v.hpa.maxReplicas) (int $v.hpa.minReplicas)) -}}
{{- fail "agent-lib: hpa needs 1 <= minReplicas <= maxReplicas" -}}
{{- end -}}
{{- if and $v.hpa.runsInFlight.enabled (lt (int $v.hpa.runsInFlight.targetAverageValue) 1) -}}
{{- fail "agent-lib: hpa.runsInFlight.targetAverageValue must be at least 1 run per pod" -}}
{{- end -}}
{{- end -}}

{{- /* Egress: default deny, with peers named by labels and ports (026 CH-4). */ -}}
{{- range $i, $rule := $v.networkPolicy.chassisEgress -}}
{{- if not (and (kindIs "map" $rule) $rule.to) -}}
{{- fail (printf "agent-lib: networkPolicy.chassisEgress[%d] has no `to`, which would allow every destination" $i) -}}
{{- end -}}
{{- range $peer := $rule.to -}}
{{- include "agent-lib.validatePeer" (dict "peer" $peer "where" (printf "networkPolicy.chassisEgress[%d]" $i)) -}}
{{- end -}}
{{- if not $rule.ports -}}
{{- fail (printf "agent-lib: networkPolicy.chassisEgress[%d] has no ports, which would open every port on the peer" $i) -}}
{{- end -}}
{{- end -}}
{{- range $i, $peer := $v.networkPolicy.ingressFrom -}}
{{- include "agent-lib.validatePeer" (dict "peer" $peer "where" (printf "networkPolicy.ingressFrom[%d]" $i)) -}}
{{- end -}}
{{- if not $v.networkPolicy.dns.podLabels -}}
{{- fail "agent-lib: networkPolicy.dns.podLabels is empty, which would open port 53 on every pod in the DNS namespace" -}}
{{- end -}}
{{- end -}}
