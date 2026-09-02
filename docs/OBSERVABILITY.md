# Observability

Infra defines composition and capacity boundaries; it does not own application
telemetry semantics.

## Responsibility split

| Signal | Owner |
| --- | --- |
| Application metrics, traces, log schema | owning application package |
| Platform component telemetry | platform addon package |
| Collector availability and retention | platform operator |
| NodePool reconcile phases | Infra provider |
| Argo CD sync/health | Argo CD and GitOps operator |

Application Helm packages should ship optional `ServiceMonitor`, `PodMonitor`,
Prometheus rules, and trace instrumentation behind explicit values. The values
must default to disabled so an operator can bring a separate telemetry stack.

The NodePool provider emits bounded JSONL events and Prometheus textfile metrics
for every phase. Use those events as benchmark evidence; do not infer safety
from a single end-to-end duration.
