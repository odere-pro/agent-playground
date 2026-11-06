{{/*
agent-lib.admission: the admission rules, rendered by the platform's chart
(deploy/helm/agent-admission) once per cluster, never by a service (the product plan, phase A;
ADR-001 hard requirement 1 and item 9; ADR-005 decisions 4 and 5).

- agent-lib.admissionPolicy: the trust rule, a ValidatingAdmissionPolicy (no controller). Its
  spec is deploy/kind/poc05/admission/policy.yaml's, byte for byte; the chart test holds the two
  equal, so the kind admission suite in CI proves the CEL the chart ships. Rules 1 to 8 are the
  lane and trust rules PoC-5 built (the header of that file explains each); rule 9 is the minimum
  chassis version.
- agent-lib.admissionBinding: Deny (and Audit) in every namespace labeled
  agents.platform/admission: enforce. The platform labels namespaces; no tenant role may. A
  service cannot switch the rules off: it has no value for them (agent-lib.validateServiceValues
  refuses `admission`), no file, and no RBAC on cluster-scoped admission objects.
- agent-lib.admissionParams: the params ConfigMap, in the platform release's namespace, which
  the binding reads (parameterNotFoundAction: Deny). registryPrefix, imageSource, and
  trustedRepositories come from the platform's values; supportedChassisTags comes from the
  library's own defaults (chassis.image.supportedTags), so the minimum chassis version is set
  once, with the chassis tag (ADR-001 item 9).

Not here: the agent-sandbox extension rules (T1 to T5, C1 to C5) and the RBAC, which are the
PoC-5 code runner's (deploy/kind/poc05/admission/).
*/}}
{{- define "agent-lib.admissionDefaults" -}}
params:
  # A registry host no public registry serves (PoC-5 review F1); a cloud sets its own.
  registryPrefix: kind.local/agent-platform/
  # pull-never on kind (every image loaded into the node); digest on a cloud (every image @sha256).
  imageSource: pull-never
  # The repositories CI builds, without a tag. Empty denies every pod (rule 0), so it is required.
  trustedRepositories: []
{{- end -}}

{{- define "agent-lib.admissionValues" -}}
{{- $defaults := include "agent-lib.admissionDefaults" . | fromYaml -}}
{{- $user := default (dict) .Values.admission | toYaml | fromYaml -}}
{{- $a := mergeOverwrite $defaults $user -}}
{{- include "agent-lib.validateAdmission" (dict "a" $a "user" $user) -}}
{{- $lib := include "agent-lib.defaults" . | fromYaml -}}
{{- $_ := set $a "supportedChassisTags" $lib.chassis.image.supportedTags -}}
{{- toYaml $a -}}
{{- end -}}

{{- define "agent-lib.validateAdmission" -}}
{{- $a := .a -}}
{{- range $k, $_ := .user -}}
{{- if ne $k "params" -}}
{{- fail (printf "agent-lib: admission.%s is not a value; the admission rules bind to every namespace labeled agents.platform/admission: enforce and cannot be switched off. Allowed: params" $k) -}}
{{- end -}}
{{- end -}}
{{- $keys := list "registryPrefix" "imageSource" "trustedRepositories" -}}
{{- range $k, $_ := $a.params -}}
{{- if not (has $k $keys) -}}
{{- fail (printf "agent-lib: admission.params.%s is not a param. Allowed: %s" $k (join ", " $keys)) -}}
{{- end -}}
{{- end -}}
{{- $p := $a.params -}}
{{- if not (regexMatch "^[a-z0-9-]+(\\.[a-z0-9-]+)+(:[0-9]+)?/([a-z0-9._-]+/)*$" (toString $p.registryPrefix)) -}}
{{- fail (printf "agent-lib: admission.params.registryPrefix %q must be a registry host with a dot, then a path ending in a slash; a bare Docker Hub namespace is anyone's (PoC-5 review F1)" (toString $p.registryPrefix)) -}}
{{- end -}}
{{- if not (has (toString $p.imageSource) (list "pull-never" "digest")) -}}
{{- fail (printf "agent-lib: admission.params.imageSource %q must be pull-never (kind: every image loaded into the node) or digest (a cloud: every image pinned by @sha256)" (toString $p.imageSource)) -}}
{{- end -}}
{{- if not (kindIs "slice" $p.trustedRepositories) -}}
{{- fail "agent-lib: admission.params.trustedRepositories must be a list of repositories" -}}
{{- end -}}
{{- if not $p.trustedRepositories -}}
{{- fail "agent-lib: admission.params.trustedRepositories is empty, and rule 0 would then deny every pod; list the repositories CI builds, without a tag" -}}
{{- end -}}
{{- range $r := $p.trustedRepositories -}}
{{- if not (hasPrefix $p.registryPrefix (toString $r)) -}}
{{- fail (printf "agent-lib: admission.params.trustedRepositories %s is outside registryPrefix %s" (toString $r) $p.registryPrefix) -}}
{{- end -}}
{{- if regexMatch "[:@]" (trimPrefix $p.registryPrefix (toString $r)) -}}
{{- fail (printf "agent-lib: admission.params.trustedRepositories %s carries a tag or a digest; list repositories only (rule 4 reads the repository, rule 9 the chassis tag)" (toString $r)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "agent-lib.admissionParams" -}}
{{- $a := include "agent-lib.admissionValues" . | fromYaml -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-trust-params
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" "agent-trust-params") | nindent 4 }}
data:
  registryPrefix: {{ $a.params.registryPrefix | quote }}
  imageSource: {{ $a.params.imageSource | quote }}
  trustedRepositories: {{ join "," $a.params.trustedRepositories | quote }}
  supportedChassisTags: {{ join "," $a.supportedChassisTags | quote }}
{{- end -}}

{{- define "agent-lib.admissionBinding" -}}
{{- $_ := include "agent-lib.admissionValues" . -}}
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicyBinding
metadata:
  name: agent-trust-rule
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" "agent-trust-rule") | nindent 4 }}
spec:
  policyName: agent-trust-rule
  validationActions: [Deny, Audit]
  paramRef:
    name: agent-trust-params
    namespace: {{ .Release.Namespace }}
    parameterNotFoundAction: Deny
  matchResources:
    namespaceSelector:
      matchLabels:
        agents.platform/admission: enforce
{{- end -}}

{{- define "agent-lib.admission" -}}
{{ include "agent-lib.admissionParams" . }}
---
{{ include "agent-lib.admissionPolicy" . }}
---
{{ include "agent-lib.admissionBinding" . }}
{{- end -}}

{{/* The policy. Everything under `spec:` is deploy/kind/poc05/admission/policy.yaml's, verbatim. */}}
{{- define "agent-lib.admissionPolicy" -}}
{{- $_ := include "agent-lib.admissionValues" . -}}
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: agent-trust-rule
  labels:
    {{- include "agent-lib.labels" (dict "root" . "name" "agent-trust-rule") | nindent 4 }}
spec:
  failurePolicy: Fail
  paramKind:
    apiVersion: v1
    kind: ConfigMap
  matchConstraints:
    # Pods are the last hop for every controller; the workload kinds are matched too, so a bad
    # Deployment or Sandbox is refused at apply time instead of failing to make pods later.
    resourceRules:
      - apiGroups: [""]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["pods", "pods/ephemeralcontainers", "replicationcontrollers"]
      - apiGroups: ["apps"]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["deployments", "replicasets", "statefulsets", "daemonsets"]
      - apiGroups: ["batch"]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["jobs", "cronjobs"]
      - apiGroups: ["agents.x-k8s.io"]
        apiVersions: ["*"]
        operations: ["CREATE", "UPDATE"]
        resources: ["sandboxes"]
      # Per-call sandbox plan, "Admission": the template's pod is checked when it is applied, not
      # only when the pool makes its first Sandbox. A SandboxClaim has no pod; the claim rule in
      # extension-policy.yaml covers it.
      - apiGroups: ["extensions.agents.x-k8s.io"]
        apiVersions: ["*"]
        operations: ["CREATE", "UPDATE"]
        resources: ["sandboxtemplates"]
  variables:
    # The pod (or pod template) the object describes.
    - name: template
      expression: >-
        request.kind.kind == 'Pod' ? object :
        request.kind.kind == 'CronJob' ? object.spec.jobTemplate.spec.template :
        request.kind.kind in ['Sandbox', 'SandboxTemplate'] ? object.spec.podTemplate :
        object.spec.template
    - name: labels
      expression: >-
        has(variables.template.metadata) && has(variables.template.metadata.labels)
        ? variables.template.metadata.labels : {}
    - name: spec
      expression: "variables.template.spec"
    - name: containers
      expression: >-
        variables.spec.containers +
        (has(variables.spec.initContainers) ? variables.spec.initContainers : []) +
        (has(variables.spec.ephemeralContainers) ? variables.spec.ephemeralContainers : [])
    - name: images
      expression: "variables.containers.map(c, has(c.image) ? c.image : '')"
    - name: data
      expression: "has(params.data) ? params.data : {}"
    - name: prefix
      expression: "variables.data[?'registryPrefix'].orValue('')"
    - name: imageSource
      expression: "variables.data[?'imageSource'].orValue('')"
    - name: trustedRepositories
      expression: >-
        variables.data[?'trustedRepositories'].orValue('').split(',')
        .map(r, r.trim()).filter(r, r != '')
    - name: chassisRepository
      expression: "variables.prefix + 'chassis'"
    - name: chassisImages
      expression: >-
        variables.images.filter(i, i == variables.chassisRepository ||
          i.startsWith(variables.chassisRepository + ':') ||
          i.startsWith(variables.chassisRepository + '@'))
    - name: workloadImages
      expression: "variables.images.filter(i, !(i in variables.chassisImages))"
    - name: trust
      expression: "variables.labels[?'agents.platform/trust'].orValue('')"
    - name: isSidecar
      expression: "size(variables.chassisImages) > 0 && size(variables.workloadImages) > 0"
    - name: isRemote
      expression: >-
        size(variables.chassisImages) == 0 &&
        (variables.labels[?'agents.platform/lane'].orValue('') == 'remote' ||
         variables.trust == 'untrusted')
    - name: shape
      expression: >-
        size(variables.chassisImages) > 0 ? 'chassis' :
        (variables.isRemote ? 'remote' : 'tool')
    - name: namespaceShape
      expression: >-
        has(namespaceObject.metadata) && has(namespaceObject.metadata.labels)
        ? namespaceObject.metadata.labels[?'agents.platform/pod-shape'].orValue('') : ''
    - name: tokenSecret
      expression: "'^[a-z0-9]([-a-z0-9]*[a-z0-9])?-token$'"
    - name: ownToken
      expression: "variables.labels[?'app.kubernetes.io/name'].orValue('') + '-token'"
    # Every Secret name the pod references, as a list of lists (no flatten needed): env per
    # container, envFrom per container, secret volumes, projected secret sources per volume.
    - name: secretNames
      expression: >-
        variables.containers.map(c, has(c.env)
          ? c.env.filter(e, has(e.valueFrom) && has(e.valueFrom.secretKeyRef))
            .map(e, has(e.valueFrom.secretKeyRef.name) ? e.valueFrom.secretKeyRef.name : '')
          : []) +
        variables.containers.map(c, has(c.envFrom)
          ? c.envFrom.filter(f, has(f.secretRef))
            .map(f, has(f.secretRef.name) ? f.secretRef.name : '')
          : []) +
        [has(variables.spec.volumes)
          ? variables.spec.volumes.filter(v, has(v.secret))
            .map(v, has(v.secret.secretName) ? v.secret.secretName : '')
          : []] +
        (has(variables.spec.volumes)
          ? variables.spec.volumes.filter(v, has(v.projected) && has(v.projected.sources))
            .map(v, v.projected.sources.filter(s, has(s.secret))
              .map(s, has(s.secret.name) ? s.secret.name : ''))
          : [])
    - name: projectedToken
      expression: >-
        has(variables.spec.volumes) && variables.spec.volumes.exists(v,
          has(v.projected) && has(v.projected.sources) &&
          v.projected.sources.exists(s, has(s.serviceAccountToken)))
    - name: secretVolumes
      expression: >-
        has(variables.spec.volumes)
        ? variables.spec.volumes.filter(v, has(v.secret) ||
            (has(v.projected) && has(v.projected.sources) &&
             v.projected.sources.exists(s, has(s.secret)))).map(v, v.name)
        : []
    # The image of every container that references a Secret.
    - name: secretUsers
      expression: >-
        variables.containers.filter(c,
          (has(c.env) && c.env.exists(e, has(e.valueFrom) && has(e.valueFrom.secretKeyRef))) ||
          (has(c.envFrom) && c.envFrom.exists(f, has(f.secretRef))) ||
          (has(c.volumeMounts) && c.volumeMounts.exists(m, m.name in variables.secretVolumes)))
        .map(c, has(c.image) ? c.image : '')
    - name: fromOwnStore
      expression: >-
        variables.imageSource == 'pull-never'
        ? variables.containers.all(c, has(c.imagePullPolicy) && c.imagePullPolicy == 'Never')
        : variables.images.all(i, i.contains('@sha256:'))
    - name: supportedChassisTags
      expression: >-
        variables.data[?'supportedChassisTags'].orValue('').split(',')
        .map(t, t.trim()).filter(t, t != '')
    # The tag of each chassis image: after the last ':' of the reference without its digest, when
    # that ':' comes after the last '/' (a registry port is before it). No tag gives ''.
    - name: chassisTags
      expression: >-
        variables.chassisImages.map(i, i.contains('@') ? i.substring(0, i.indexOf('@')) : i)
        .map(r, r.lastIndexOf(':') > r.lastIndexOf('/') ? r.substring(r.lastIndexOf(':') + 1) : '')
  validations:
    - expression: >-
        variables.prefix != '' && size(variables.trustedRepositories) > 0 &&
        variables.imageSource in ['pull-never', 'digest']
      message: >-
        trust rule 0: the params agent-platform-system/agent-trust-params must set registryPrefix,
        trustedRepositories, and imageSource
      reason: Forbidden
    - expression: "variables.trust in ['trusted', 'untrusted']"
      message: >-
        trust rule 1: every pod must carry the label agents.platform/trust with trusted or
        untrusted
      reason: Forbidden
    - expression: "!(variables.isSidecar && variables.trust == 'untrusted')"
      message: >-
        trust rule 2: an untrusted workload may not run in the sidecar lane; use the remote lane
      reason: Forbidden
    - expression: "variables.isRemote || variables.images.all(i, i.startsWith(variables.prefix))"
      message: >-
        trust rule 3: outside the remote lane every image must come from the platform registry
        prefix
      messageExpression: >-
        'trust rule 3: outside the remote lane every image must come from the platform registry
        prefix: ' + variables.images.filter(i, !i.startsWith(variables.prefix)).join(', ')
      reason: Forbidden
    - expression: >-
        !(variables.isSidecar && variables.trust == 'trusted') ||
        variables.workloadImages.filter(i, i.startsWith(variables.prefix)).all(i,
          variables.trustedRepositories.exists(r,
            i == r || i.startsWith(r + ':') || i.startsWith(r + '@')))
      message: >-
        trust rule 4: a trusted sidecar-lane pod may run only workload images from the
        trustedRepositories in agent-trust-params
      messageExpression: >-
        'trust rule 4: a trusted sidecar-lane pod may run only workload images from the
        trustedRepositories in agent-trust-params: ' +
        variables.workloadImages.filter(i, i.startsWith(variables.prefix) &&
          !variables.trustedRepositories.exists(r,
            i == r || i.startsWith(r + ':') || i.startsWith(r + '@'))).join(', ')
      reason: Forbidden
    - expression: >-
        !variables.isRemote ||
        (has(variables.spec.runtimeClassName) && variables.spec.runtimeClassName == 'gvisor' &&
         has(variables.spec.automountServiceAccountToken) &&
         variables.spec.automountServiceAccountToken == false &&
         !variables.projectedToken &&
         variables.secretNames.all(l, l.all(n, n.matches(variables.tokenSecret))))
      message: >-
        trust rule 5: a remote workload pod must set runtimeClassName gvisor and
        automountServiceAccountToken false, and take no Secret but its own <name>-token
      reason: Forbidden
    - expression: "size(variables.chassisImages) <= 1"
      message: "trust rule 6a: a pod may run at most one chassis container"
      reason: Forbidden
    - expression: >-
        variables.containers.all(c, !has(c.image) || !(c.image in variables.chassisImages) ||
          !has(c.command) || size(c.command) == 0)
      message: >-
        trust rule 6b: the chassis container may not override the image command
      reason: Forbidden
    - expression: >-
        variables.isRemote || variables.secretUsers.all(i, i in variables.chassisImages)
      message: >-
        trust rule 6c: outside the remote lane only the chassis container may reference a Secret
      reason: Forbidden
    - expression: "variables.shape == variables.namespaceShape"
      message: >-
        trust rule 7a: the pod shape (chassis, remote, or tool) must match the
        agents.platform/pod-shape label of its namespace
      messageExpression: >-
        'trust rule 7a: the pod shape (chassis, remote, or tool) must match the
        agents.platform/pod-shape label of its namespace: ' + variables.shape + ' in ' +
        (variables.namespaceShape == '' ? 'none' : variables.namespaceShape)
      reason: Forbidden
    - expression: >-
        variables.labels[?'agents.platform/role'].orValue('') != 'chassis' ||
        size(variables.chassisImages) > 0
      message: >-
        trust rule 7b: only a pod that runs the chassis image may carry agents.platform/role:
        chassis
      reason: Forbidden
    - expression: >-
        !variables.isRemote || variables.secretNames.all(l, l.all(n,
          !n.matches(variables.tokenSecret) || n == variables.ownToken))
      message: >-
        trust rule 7c: a remote pod may take only its own token, the Secret named after its
        app.kubernetes.io/name label plus -token
      reason: Forbidden
    - expression: >-
        variables.shape != 'tool' ||
        (has(variables.spec.runtimeClassName) && variables.spec.runtimeClassName == 'gvisor' &&
         has(variables.spec.automountServiceAccountToken) &&
         variables.spec.automountServiceAccountToken == false)
      message: >-
        trust rule 7d: a tool pod must set runtimeClassName gvisor and
        automountServiceAccountToken false
      reason: Forbidden
    - expression: "variables.isRemote || variables.fromOwnStore"
      message: >-
        trust rule 8: outside the remote lane every image must come from the cluster image store:
        imagePullPolicy Never, or an @sha256 digest when imageSource is digest
      reason: Forbidden
    - expression: "variables.chassisTags.all(t, t in variables.supportedChassisTags)"
      message: >-
        trust rule 9: every chassis image must carry a tag from supportedChassisTags in
        agent-trust-params, the chassis tags at or above the minimum version; a chassis below it,
        or pinned by digest alone, is refused
      messageExpression: >-
        'trust rule 9: every chassis image must carry a tag from supportedChassisTags in
        agent-trust-params, the chassis tags at or above the minimum version; a chassis below it,
        or pinned by digest alone, is refused: ' +
        variables.chassisTags.filter(t, !(t in variables.supportedChassisTags)).join(', ')
      reason: Forbidden
{{- end -}}
