#!/usr/bin/env bash
# Publish a bounded Prometheus textfile from a provider reconciliation.
set -euo pipefail

action="${1:-}"
shift || true
metrics="" kubeconfig="" context=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --metrics) metrics="${2:?missing metrics path}"; shift 2 ;;
    --kubeconfig) kubeconfig="${2:?missing kubeconfig path}"; shift 2 ;;
    --context) context="${2:?missing context}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$action" = publish ] && [ -s "$metrics" ] && [ -s "$kubeconfig" ] && [ -n "$context" ] || {
  echo 'usage: nodepool-metrics.sh publish --metrics FILE --kubeconfig FILE --context NAME' >&2
  exit 2
}

kube=(kubectl --kubeconfig "$kubeconfig" --context "$context")
"${kube[@]}" create namespace observability --dry-run=client -o yaml | "${kube[@]}" apply -f - >/dev/null
"${kube[@]}" -n observability create configmap infra-nodepool-metrics \
  --from-file=metrics.prom="$metrics" --dry-run=client -o yaml | "${kube[@]}" apply -f - >/dev/null
"${kube[@]}" -n observability apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: infra-nodepool-metrics
  labels:
    app.kubernetes.io/name: infra-nodepool-metrics
    app.kubernetes.io/managed-by: infra-nodepool
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: infra-nodepool-metrics
  template:
    metadata:
      labels:
        app.kubernetes.io/name: infra-nodepool-metrics
        app.kubernetes.io/managed-by: infra-nodepool
    spec:
      automountServiceAccountToken: false
      nodeSelector:
        infra.convee.io/node-role: system
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      containers:
        - name: metrics
          image: busybox:1.37.0@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0
          args: [httpd, -f, -p, "9100", -h, /metrics]
          ports:
            - {name: metrics, containerPort: 9100}
          readinessProbe:
            httpGet: {path: /metrics.prom, port: metrics}
          resources:
            requests: {cpu: 5m, memory: 8Mi}
            limits: {cpu: 50m, memory: 32Mi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: metrics, mountPath: /metrics, readOnly: true}
      securityContext:
        runAsNonRoot: true
        runAsUser: 65534
        seccompProfile: {type: RuntimeDefault}
      volumes:
        - name: metrics
          configMap: {name: infra-nodepool-metrics}
---
apiVersion: v1
kind: Service
metadata:
  name: infra-nodepool-metrics
  labels:
    app.kubernetes.io/name: infra-nodepool-metrics
    app.kubernetes.io/managed-by: infra-nodepool
spec:
  selector:
    app.kubernetes.io/name: infra-nodepool-metrics
  ports:
    - {name: metrics, port: 9100, targetPort: metrics}
YAML
"${kube[@]}" -n observability rollout status deployment/infra-nodepool-metrics --timeout=3m
