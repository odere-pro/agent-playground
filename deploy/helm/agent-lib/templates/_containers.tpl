{{/*
The containers. Each takes (dict "root" $ "v" <merged values> "lane" <sidecar|remote>) and prints
one list item at column 0; the caller indents it.

The chassis (uid 10001) is the only container with a Secret: chassis.secretEnv,
chassis.envFromSecrets, chassis.secretVolumes, and in the remote lane REMOTE_TOKEN. Its public
port binds the pod IP, never 0.0.0.0, so loopback carries only the proxies (H13). The image's
ENTRYPOINT is `chassis serve`; the chart sets args only, never a command (admission rule 6b).
*/}}
{{- define "agent-lib.chassisContainer" -}}
{{- $v := .v -}}
{{- $c := $v.chassis -}}
- name: chassis
  image: {{ include "agent-lib.chassisImage" $c.image | quote }}
  imagePullPolicy: {{ $c.image.pullPolicy }}
  {{- if eq .lane "sidecar" }}
  # The native sidecar: an init container that keeps running. The kubelet starts the workload
  # only after this startupProbe passes, so the chassis binds its proxies first (contract v4 H14).
  restartPolicy: Always
  {{- end }}
  args:
    - --config
    - /etc/chassis/config.yaml
    - --host
    - $(POD_IP)
    - --port
    - {{ int $c.port | quote }}
    {{- if eq .lane "remote" }}
    - --remote-proxy-host
    - $(POD_IP)
    - --remote-proxy-port
    - {{ int $c.remoteProxyPort | quote }}
    {{- end }}
    - --startup-wait-s
    - {{ int $c.startupWaitSeconds | quote }}
    - --drain-delay-s
    - {{ int $c.drainDelaySeconds | quote }}
    - --drain-timeout-s
    - {{ int $c.drainTimeoutSeconds | quote }}
    {{- range $c.extraArgs }}
    - {{ toString . | quote }}
    {{- end }}
  env:
    - name: POD_IP
      valueFrom:
        fieldRef:
          fieldPath: status.podIP
    {{- range $c.env }}
    - name: {{ .name | quote }}
      value: {{ toString .value | quote }}
    {{- end }}
    {{- range $c.secretEnv }}
    - name: {{ .name | quote }}
      valueFrom:
        secretKeyRef:
          name: {{ .secretName | quote }}
          key: {{ .key | quote }}
    {{- end }}
    {{- if eq .lane "remote" }}
    - name: REMOTE_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ $v.remote.tokenSecret.name | quote }}
          key: {{ $v.remote.tokenSecret.key | quote }}
    {{- end }}
  {{- with $c.envFromSecrets }}
  envFrom:
    {{- range . }}
    - secretRef:
        name: {{ . | quote }}
    {{- end }}
  {{- end }}
  ports:
    - name: http
      containerPort: {{ int $c.port }}
      protocol: TCP
    {{- if eq .lane "remote" }}
    - name: remote-proxy
      containerPort: {{ int $c.remoteProxyPort }}
      protocol: TCP
    {{- end }}
  # `/health` answers once the proxies are bound; `/ready` is 503 until the workload answers.
  startupProbe:
    httpGet:
      path: /health
      port: {{ int $c.port }}
    periodSeconds: 1
    failureThreshold: 60
  readinessProbe:
    httpGet:
      path: /ready
      port: {{ int $c.port }}
    periodSeconds: 2
    timeoutSeconds: 1
    failureThreshold: 1
  livenessProbe:
    httpGet:
      path: /health
      port: {{ int $c.port }}
    periodSeconds: 10
    timeoutSeconds: 3
    failureThreshold: 3
  resources:
    {{- toYaml $c.resources | nindent 4 }}
  securityContext:
    {{- include "agent-lib.containerSecurityContext" 10001 | nindent 4 }}
  volumeMounts:
    - name: chassis-tmp
      mountPath: /tmp
    - name: chassis-config
      mountPath: /etc/chassis
      readOnly: true
    {{- range $c.secretVolumes }}
    - name: {{ printf "secret-%s" .name | quote }}
      mountPath: {{ .mountPath | quote }}
      readOnly: true
    {{- end }}
{{- end -}}

{{/*
The workload in the sidecar lane (uid 10002): plain env only, no Secret, no port in the pod spec.
It listens on 127.0.0.1 and reaches the chassis's proxies on loopback. The kubelet's httpGet goes
to the pod IP, so its probes exec a GET on the agent card (suggested: python; set
workload.probeCommand for another runtime). The liveness probe has its own grace of 10 s, so a
hung process restarts the pod without waiting for the pod's grace (024 CH-3, PoC-4 status).
*/}}
{{- define "agent-lib.workloadContainer" -}}
{{- $v := .v -}}
{{- $w := $v.workload -}}
{{- $cmd := $w.probeCommand -}}
{{- if not $cmd -}}
{{- $cmd = list "python" "-c" (printf "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%d/.well-known/agent-card.json', timeout=2).status == 200 else 1)" (int $w.port)) -}}
{{- end -}}
- name: workload
  image: {{ $w.image | quote }}
  imagePullPolicy: {{ $w.pullPolicy }}
  {{- with $w.args }}
  args:
    {{- range . }}
    - {{ toString . | quote }}
    {{- end }}
  {{- end }}
  env:
    - name: HOST
      value: "127.0.0.1"
    - name: PORT
      value: {{ int $w.port | quote }}
    - name: CHASSIS_MODEL_URL
      value: {{ printf "http://127.0.0.1:%d/v1" (int $v.chassis.proxyPort) | quote }}
    - name: CHASSIS_TOOL_URL
      value: {{ printf "http://127.0.0.1:%d/mcp" (int $v.chassis.proxyPort) | quote }}
    {{- range $w.env }}
    - name: {{ .name | quote }}
      value: {{ toString .value | quote }}
    {{- end }}
  startupProbe:
    exec:
      command: {{ toJson $cmd }}
    periodSeconds: 2
    timeoutSeconds: 3
    failureThreshold: 30
  livenessProbe:
    exec:
      command: {{ toJson $cmd }}
    periodSeconds: 10
    timeoutSeconds: 3
    failureThreshold: 3
    terminationGracePeriodSeconds: 10
  resources:
    {{- toYaml $w.resources | nindent 4 }}
  securityContext:
    {{- include "agent-lib.containerSecurityContext" 10002 | nindent 4 }}
  volumeMounts:
    - name: workload-tmp
      mountPath: /tmp
{{- end -}}

{{/*
The workload in the remote lane (uid 10002), in its own pod under gVisor. Its one Secret is its
own bearer token, CHASSIS_API_TOKEN (ADR-005 decision 1: "no secrets mounted" means no provider
key and no internal credential). It listens on the pod IP with the token required on every path
(`--require-token-env`, the Python template only in PoC-5) and has no DNS, so the chassis's
remote proxy is a fixed ClusterIP given as plain env. Probes are TCP: the kubelet has no token.
*/}}
{{- define "agent-lib.remoteWorkloadContainer" -}}
{{- $v := .v -}}
{{- $w := $v.workload -}}
{{- $proxy := printf "http://%s:%d" (toString $v.remote.proxyClusterIP) (int $v.chassis.remoteProxyPort) -}}
- name: workload
  image: {{ $w.image | quote }}
  imagePullPolicy: {{ $w.pullPolicy }}
  args:
    - --require-token-env
    - CHASSIS_API_TOKEN
    {{- range $w.args }}
    - {{ toString . | quote }}
    {{- end }}
  env:
    - name: POD_IP
      valueFrom:
        fieldRef:
          fieldPath: status.podIP
    - name: HOST
      value: $(POD_IP)
    - name: PORT
      value: {{ int $w.port | quote }}
    - name: CHASSIS_MODEL_URL
      value: {{ printf "%s/v1" $proxy | quote }}
    - name: CHASSIS_TOOL_URL
      value: {{ printf "%s/mcp" $proxy | quote }}
    - name: CHASSIS_API_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ $v.remote.tokenSecret.name | quote }}
          key: {{ $v.remote.tokenSecret.key | quote }}
    {{- range $w.env }}
    - name: {{ .name | quote }}
      value: {{ toString .value | quote }}
    {{- end }}
  ports:
    - name: a2a
      containerPort: {{ int $w.port }}
      protocol: TCP
  startupProbe:
    tcpSocket:
      port: {{ int $w.port }}
    periodSeconds: 2
    failureThreshold: 60
  readinessProbe:
    tcpSocket:
      port: {{ int $w.port }}
    periodSeconds: 5
  livenessProbe:
    tcpSocket:
      port: {{ int $w.port }}
    periodSeconds: 10
    failureThreshold: 3
  resources:
    {{- toYaml $w.resources | nindent 4 }}
  securityContext:
    {{- include "agent-lib.containerSecurityContext" 10002 | nindent 4 }}
  volumeMounts:
    - name: workload-tmp
      mountPath: /tmp
{{- end -}}
