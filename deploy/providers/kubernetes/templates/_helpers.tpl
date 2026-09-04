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
{{- $tag := .Values.flClient.image.tag }}
{{- if eq .Values.flBackend "nvflare" }}
{{- printf "%s/flare-fl-client:%s" $registry $tag }}
{{- else }}
{{- printf "%s/flower-supernode:%s" $registry $tag }}
{{- end }}
{{- end }}

{{/*
Whether the FL client's participant kit is fetched from S3 by the kit-init
initContainer, for the ACTIVE backend. Returns a non-empty string when true and
an empty string (falsy) otherwise.

Both the kit-init initContainer and the fl-client-kit volume must agree on this:
when the kit comes from S3 the volume is an emptyDir that kit-init populates,
otherwise it is the hostPath at flClient.kitHostPath. Gating one of them on a
single backend's flag lets the two disagree, which renders an empty volume and
drops a staged kit silently (#999) — so both call this helper.
*/}}
{{- define "flip-trust.flClientKitFromS3" -}}
{{- if (index .Values.flClient .Values.flBackend).kitFromS3.enabled }}true{{ end }}
{{- end }}

{{/*
Refuse a real-PACS install whose DICOM receiver has no route from outside the cluster.

Retrieval is two connections in opposite directions: XNAT dials the PACS to C-FIND and C-MOVE, then
the PACS opens a *new* association back to XNAT to C-STORE the studies. templates/xnat-web.yaml
publishes the receiver beyond the cluster only when service.type is NodePort AND dicomNodePort is
set — on ClusterIP both blocks silently no-op, so the render succeeds and produces the failure this
chart calls the hardest to diagnose: queries succeed, retrievals silently time out with nothing
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
reaches the pod, and `externalTrafficPolicy: Local` is gated on the same pair (xnat-web.yaml:56),
so kube-proxy SNATs the source address and the ingress CIDR cannot match even if a packet did
arrive. That is the same queries-succeed / retrievals-time-out failure, reached through the one
combination the other refusals leave open.
*/}}
{{- define "flip-trust.validatePacsReachable" -}}
{{- if and .Values.xnat.enabled .Values.xnat.web.enabled (ne .Values.pacs.host "orthanc") }}
{{- if eq .Values.xnat.web.service.type "ClusterIP" }}
{{- fail (printf "pacs.host is %s but xnat.web.service.type is ClusterIP, so the DICOM receiver is unreachable from outside the cluster and the C-STORE return leg the PACS opens after C-MOVE can never arrive. Set xnat.web.service.type: NodePort plus xnat.web.dicomNodePort (equal to xnat.web.dicomPort), or expose the receiver through a LoadBalancer service." .Values.pacs.host) }}
{{- end }}
{{- if and (eq .Values.xnat.web.service.type "NodePort") (not .Values.xnat.web.dicomNodePort) }}
{{- fail (printf "pacs.host is %s and xnat.web.service.type is NodePort, but xnat.web.dicomNodePort is unset, so the receiver's node port is allocated at random and externalTrafficPolicy stays Cluster. The PACS cannot be given a stable destination port, and kube-proxy rewrites its source address so the ingress NetworkPolicy CIDR never matches — queries succeed and retrievals silently time out. Set xnat.web.dicomNodePort (equal to xnat.web.dicomPort, %v), widening the API server's --service-node-port-range if needed." .Values.pacs.host (.Values.xnat.web.dicomPort | default 8104)) }}
{{- end }}
{{- end }}
{{- end }}
