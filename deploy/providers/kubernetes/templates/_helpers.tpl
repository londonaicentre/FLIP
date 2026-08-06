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
Whether the FL participant kit is fetched from S3, for the ACTIVE backend.
The init container and the kit volume must agree on this: reading nvflare's flag
while running Flower fetched an S3 kit into a hostPath volume (and vice versa).
*/}}
{{- define "flip-trust.flKitFromS3" -}}
{{- if eq .Values.flBackend "flower" }}
{{- .Values.flClient.flower.kitFromS3.enabled }}
{{- else }}
{{- .Values.flClient.nvflare.kitFromS3.enabled }}
{{- end }}
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
