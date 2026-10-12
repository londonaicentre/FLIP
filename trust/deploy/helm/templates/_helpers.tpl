{{/*
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/}}

{{/*
The image tag a FLIP-built service runs (FLIP#1204): the image's `pin` when set, else the
site-wide release pin `global.image.tag`, else the service's own `image.tag`. Every FLIP
image line goes through this so `make upgrade-trust-k8s TAG=vX.Y.Z` moves the whole site at
once — a template reading `.Values.<svc>.image.tag` directly would keep that one service on
the old release with no error (tests/test_chart_template_invariants.py guards the calls,
tests/templates/test_helpers.py the rendered precedence).
The optional `pin` is the Kubernetes twin of the compose files' OMOP_DB_TAG / ORTHANC_TAG /
XNAT_TAG / FL_TAG opt-outs (`omopDb.image.pin`, `orthanc.image.pin`, `xnat.image.pin`,
`flClient.image.pin`): those images' CI builds are path-filtered, so a sha- tag of a commit
that never touched them has no such image, and the pin keeps that one image where it is
while the rest of the site moves. Release tags build every image, so a release upgrade
never needs one.
Usage: {{ include "flip-trust.imageTag" (dict "global" .Values.global.image.tag "own" .Values.trustApi.image.tag) }}
       {{ include "flip-trust.imageTag" (dict "pin" .Values.omopDb.image.pin "global" .Values.global.image.tag "own" .Values.omopDb.image.tag) }}
*/}}
{{- define "flip-trust.imageTag" -}}
{{- .pin | default .global | default .own }}
{{- end }}

{{/*
Expand the name of the chart.
*/}}
{{- define "flip-trust.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "flip-trust.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "flip-trust.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "flip-trust.labels" -}}
helm.sh/chart: {{ include "flip-trust.chart" . }}
{{ include "flip-trust.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "flip-trust.selectorLabels" -}}
app.kubernetes.io/name: {{ include "flip-trust.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "flip-trust.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "flip-trust.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image pull secrets helper
*/}}
{{- define "flip-trust.imagePullSecrets" -}}
{{- range .Values.imagePullSecrets }}
- name: {{ .name }}
{{- end }}
{{- end }}

{{/*
Namespace name
*/}}
{{- define "flip-trust.namespace" -}}
{{- if .Values.namespace.name }}
{{- .Values.namespace.name }}
{{- else }}
{{- .Release.Namespace }}
{{- end }}
{{- end }}

{{/*
OMOP database connection environment, shared by the vocab-load Job's probe and
loader containers. Both talk to the same database with the same credentials —
keeping one definition means a rename here cannot leave the probe reading a
different database than the loader writes to.
*/}}
{{- define "flip-trust.omopVocabDbEnv" -}}
- name: OMOP_DB_HOST
  value: "omop-db"
- name: OMOP_DB_PORT
  value: "5432"
- name: OMOP_POSTGRES_USER
  value: {{ .Values.omopDb.credentials.user | quote }}
- name: OMOP_POSTGRES_DB
  value: {{ .Values.dataAccessApi.env.OMOP_POSTGRES_DB | quote }}
- name: OMOP_POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ if .Values.secrets.create }}{{ include "flip-trust.fullname" . }}-secrets{{ else }}{{ .Values.secrets.existingName }}{{ end }}
      key: omop-postgres-password
{{- end }}

{{/*
FL client image name based on backend selection
*/}}
{{- define "flip-trust.flClientImage" -}}
{{- $registry := .Values.flClient.image.repository }}
{{- $tag := include "flip-trust.imageTag" (dict "pin" .Values.flClient.image.pin "global" .Values.global.image.tag "own" .Values.flClient.image.tag) }}
{{- if eq .Values.flBackend "nvflare" }}
{{- printf "%s/flare-fl-client:%s" $registry $tag }}
{{- else }}
{{- printf "%s/flower-supernode:%s" $registry $tag }}
{{- end }}
{{- end }}

{{/*
Refuse a real-PACS install whose DICOM receiver has no route from outside the cluster.

Retrieval is two connections in opposite directions: XNAT dials the PACS to C-FIND and C-MOVE, then
the PACS opens a *new* association back to XNAT to C-STORE the studies. templates/xnat-web.yaml
publishes the receiver beyond the cluster only when dicomService.type is NodePort AND dicomNodePort
is set — on ClusterIP both blocks silently no-op, so the render succeeds and produces the failure
this chart calls the hardest to diagnose: queries succeed, retrievals silently time out with nothing
logged on either side.

This lives here, and is included from xnat-web.yaml, rather than in network-policy.yaml where it
started: it is a statement about the Service's exposure, not about NetworkPolicy. Inside that file
it inherited `if .Values.networkPolicies.enabled`, so an install that turns policies off — supported,
and the right call on a CNI that does not enforce them — rendered cleanly on ClusterIP with no path
a PACS packet could take, which is precisely the case the guard exists to stop.

Refuses ClusterIP specifically rather than demanding NodePort, because a LoadBalancer service
(MetalLB and friends) is an equally valid way to make the receiver reachable and must not be
rejected.

Both halves of that conjunction are checked, not just the Service type. NodePort with
`dicomNodePort` left at its empty default renders clean while breaking the same leg twice over:
Kubernetes allocates a random NodePort, so the port the PACS was told to dial is not the one that
reaches the pod, and the `nodePort:` pin is gated on the same pair (the `dicomNodePort` + NodePort
conjunction in xnat-web.yaml's xnat-web-dicom Service), so the PACS is given a destination port
nothing is listening on. `externalTrafficPolicy: Local` does render — it is gated on the type alone
— but with a random node port no packet reaches it. That is the same queries-succeed /
retrievals-time-out failure, reached through the one combination the other refusals leave open.

The web console is held at ClusterIP for a real-PACS install, rather than only being documented as
such. The split means `xnat.web.service.type` no longer has anything to do with DICOM reachability,
so the one reason an operator ever had to raise it is gone — but an install that set it to NodePort
under the *old* single-Service chart carries that value across the upgrade, where it now exposes the
Tomcat console (session cookies, the admin login, the whole archive's metadata) on a node address
instead of the DICOM port it was set for. That is a silent widening on upgrade, in the one direction
this split exists to make impossible, so it is refused with a message that names the upgrade as the
likely cause. It is scoped to a real PACS for the same reason every other check here is: a mocked
Orthanc install is a developer's cluster, and `make port-forward` is not the only legitimate way to
reach a console there.

The wide-open-CIDR check at the end of this partial is a tripwire, not a CIDR validator: Go
templates cannot evaluate a CIDR, so it compares each entry against the exact literal 0.0.0.0/0
(whitespace trimmed) and nothing else. 0.0.0.0/1, ::/0 and any other prefix that still reaches the
PACS pass it. It is here because the one live install this was found on used that literal, and
catching the honest form of the mistake is worth more than claiming a completeness it does not
have — the fail message says as much, so an operator reading it is not misled into thinking the
chart has validated their CIDR. The rule that forbids a wide scope is NETWORK-POLICY.md's, and it
binds whether or not this check can see it.
*/}}
{{- define "flip-trust.validatePacsReachable" -}}
{{- if and .Values.xnat.enabled .Values.xnat.web.enabled (ne .Values.pacs.host "orthanc") }}
{{- if eq .Values.xnat.web.dicomService.type "ClusterIP" }}
{{- fail (printf "pacs.host is %s but xnat.web.dicomService.type is ClusterIP, so the DICOM receiver is unreachable from outside the cluster and the C-STORE return leg the PACS opens after C-MOVE can never arrive. Set xnat.web.dicomService.type: NodePort plus xnat.web.dicomNodePort (equal to xnat.web.dicomPort), or set it to LoadBalancer." .Values.pacs.host) }}
{{- end }}
{{- if ne .Values.xnat.web.service.type "ClusterIP" }}
{{- fail (printf "pacs.host is %s but xnat.web.service.type is %s. That Service is the Tomcat web console, not the DICOM receiver: exposing it puts the admin login and the archive's metadata on an external address, and it does nothing for retrieval. If you are upgrading an install that set this to reach DICOM, that is what it used to do — the DICOM SCP now has its own Service, so move the value to xnat.web.dicomService.type and return xnat.web.service.type to ClusterIP. Reach the console with `kubectl port-forward` (see the chart README) rather than by exposing it." .Values.pacs.host .Values.xnat.web.service.type) }}
{{- end }}
{{- if and (eq .Values.xnat.web.dicomService.type "NodePort") (not .Values.xnat.web.dicomNodePort) }}
{{- fail (printf "pacs.host is %s and xnat.web.dicomService.type is NodePort, but xnat.web.dicomNodePort is unset, so the receiver's node port is allocated at random. The PACS cannot be given a stable destination port — it is told to dial xnat.web.dicomPort while the node answers on whatever Kubernetes picked, so queries succeed and retrievals silently time out. Set xnat.web.dicomNodePort (equal to xnat.web.dicomPort, %v), widening the API server's --service-node-port-range if needed." .Values.pacs.host (.Values.xnat.web.dicomPort | default 8104)) }}
{{- end }}
{{- if and (ne .Values.xnat.web.dicomService.type "ClusterIP") .Values.networkPolicies.enabled }}
{{- $wideOpen := false }}
{{- range .Values.networkPolicies.allowedIngressCIDRsWithPorts }}
{{- range .cidrs | default list }}
{{- if eq (trim .) "0.0.0.0/0" }}
{{- $wideOpen = true }}
{{- end }}
{{- end }}
{{- end }}
{{- if $wideOpen }}
{{- fail (printf "pacs.host is %s and networkPolicies.allowedIngressCIDRsWithPorts contains 0.0.0.0/0 for the DICOM port — that opens it to the entire internet rather than the PACS itself, which NETWORK-POLICY.md explicitly says never to do. Scope the CIDR to the PACS's actual source address. (This check matches the literal 0.0.0.0/0 only, whitespace aside: Helm cannot evaluate a CIDR, so 0.0.0.0/1, ::/0 and any other prefix that still reaches the PACS are not caught here — the rule against a wide scope is what forbids them, not this check.)" .Values.pacs.host) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
