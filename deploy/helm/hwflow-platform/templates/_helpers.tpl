{{/* Resource name of a component: <release>-<component>. Services use the same names (DNS). */}}
{{- define "hwp.name" -}}
{{- printf "%s-%s" .root.Release.Name .c | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "hwp.labels" -}}
app.kubernetes.io/name: hwflow-platform
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .c }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version }}
{{- end -}}

{{- define "hwp.selector" -}}
app.kubernetes.io/name: hwflow-platform
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .c }}
{{- end -}}

{{- define "hwp.image" -}}
{{- printf "%s:%s" .repository (toString .tag) -}}
{{- end -}}

{{- define "hwp.pullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* priorityClassName: component value, else the global one. Usage: include "hwp.priority" (dict "root" . "v" .Values.api) */}}
{{- define "hwp.priority" -}}
{{- $p := default .root.Values.global.priorityClassName .v.priorityClassName -}}
{{- if $p }}
priorityClassName: {{ $p }}
{{- end }}
{{- end -}}

{{/* env var from the platform Secret */}}
{{- define "hwp.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.secrets.name }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}

{{/* Passwords first: later entries reference them with $(VAR). Passwords must be URL-safe (hex). */}}
{{- define "hwp.dbPasswordsEnv" -}}
{{ include "hwp.secretEnv" (dict "root" .root "name" "DB_OWNER_PASSWORD" "key" "hwflow-db-password") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "DB_APP_PASSWORD" "key" "hwflow-app-db-password") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "DB_RELAY_PASSWORD" "key" "hwflow-relay-db-password") }}
{{- end -}}

{{/* Settings shared by api, worker, migrate, purge (hwplatform.settings, prefix HWPLATFORM_). */}}
{{- define "hwp.appEnv" -}}
{{- $pg := include "hwp.name" (dict "root" .root "c" "postgres") -}}
{{- $s3 := include "hwp.name" (dict "root" .root "c" "seaweedfs") -}}
{{- $nats := include "hwp.name" (dict "root" .root "c" "nats") -}}
{{- $tmp := include "hwp.name" (dict "root" .root "c" "temporal") -}}
{{ include "hwp.dbPasswordsEnv" . }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "HWPLATFORM_S3_ACCESS_KEY" "key" "s3-access-key") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "HWPLATFORM_S3_SECRET_KEY" "key" "s3-secret-key") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "HWPLATFORM_JWT_SECRET" "key" "jwt-secret") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "HWPLATFORM_MASTER_KEY" "key" "master-key") }}
{{ include "hwp.secretEnv" (dict "root" .root "name" "HWPLATFORM_MASTER_KEYS" "key" "master-keys" "optional" true) }}
- {name: HWPLATFORM_DATABASE_URL, value: "postgresql+asyncpg://hwflow_app:$(DB_APP_PASSWORD)@{{ $pg }}:5432/hwflow"}
- {name: HWPLATFORM_MIGRATION_DATABASE_URL, value: "postgresql+asyncpg://hwflow:$(DB_OWNER_PASSWORD)@{{ $pg }}:5432/hwflow"}
- {name: HWPLATFORM_RELAY_DATABASE_URL, value: "postgresql+asyncpg://hwflow_relay:$(DB_RELAY_PASSWORD)@{{ $pg }}:5432/hwflow"}
- {name: HWPLATFORM_S3_ENDPOINT_URL, value: "http://{{ $s3 }}:8333"}
- {name: HWPLATFORM_S3_BUCKET, value: {{ .root.Values.seaweedfs.bucket | quote }}}
- {name: HWPLATFORM_NATS_URL, value: "nats://{{ $nats }}:4222"}
- {name: HWPLATFORM_TEMPORAL_ADDRESS, value: "{{ $tmp }}:7233"}
- {name: HWPLATFORM_TEMPORAL_NAMESPACE, value: {{ .root.Values.temporal.namespace | quote }}}
- {name: HWPLATFORM_PUBLIC_URL, value: {{ .root.Values.global.publicUrl | quote }}}
{{- end -}}

{{/* wait until TCP host:port accepts connections, using only the python in the app image */}}
{{- define "hwp.waitTcp" -}}
until python -c "import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 2)" {{ .host }} {{ .port }}; do echo "waiting for {{ .host }}:{{ .port }}"; sleep 2; done
{{- end -}}
