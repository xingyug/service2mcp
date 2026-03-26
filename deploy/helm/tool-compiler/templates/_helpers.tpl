{{- define "tool-compiler.name" -}}
tool-compiler
{{- end -}}

{{- define "tool-compiler.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tool-compiler.commonLabels" -}}
app.kubernetes.io/name: {{ include "tool-compiler.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "tool-compiler.componentLabels" -}}
{{ include "tool-compiler.commonLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "tool-compiler.componentSelectorLabels" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
