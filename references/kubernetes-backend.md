# Kubernetes Backend Reference

Patterns for running multi-agent workflows as Kubernetes Jobs.

## Prerequisites

- Kubernetes cluster with kubectl access
- `WARP_API_KEY` stored as a Kubernetes Secret
- Namespace for agent workloads (default: `warp-agents`)

## Initial Setup

```bash
# Create namespace
kubectl create namespace warp-agents

# Store API key as secret
kubectl create secret generic warp-api-key \
  --namespace warp-agents \
  --from-literal=WARP_API_KEY="$WARP_API_KEY"

# Create shared PVC for workspace
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: agent-workspace
  namespace: warp-agents
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 10Gi
EOF
```

## Job Definition

### Basic Agent Job

```yaml
# agent-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: agent-${TASK_ID}
  namespace: warp-agents
  labels:
    app: warp-agent
    task-id: ${TASK_ID}
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: agent
          image: warpdotdev/dev-base:latest
          workingDir: /workspace
          command:
            - oz
            - agent
            - run
            - --prompt
            - "${PROMPT}"
            - --share
            - team
          env:
            - name: WARP_API_KEY
              valueFrom:
                secretKeyRef:
                  name: warp-api-key
                  key: WARP_API_KEY
            - name: TASK_ID
              value: "${TASK_ID}"
          volumeMounts:
            - name: workspace
              mountPath: /workspace
            - name: output
              mountPath: /output
          resources:
            requests:
              memory: "2Gi"
              cpu: "1"
            limits:
              memory: "4Gi"
              cpu: "2"
      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: agent-workspace
        - name: output
          emptyDir: {}
```

### Spawn Job via Script

```bash
python3 <skill_dir>/scripts/spawn_k8s.py \
  --tasks "Analyze auth" "Analyze API" "Analyze DB" \
  --namespace warp-agents \
  --image warpdotdev/dev-base:latest
```

## Spawning Patterns

### Parallel Jobs with ConfigMap

```bash
# Create prompts ConfigMap
kubectl create configmap agent-prompts \
  --namespace warp-agents \
  --from-literal=task-1="Analyze authentication module" \
  --from-literal=task-2="Analyze database layer" \
  --from-literal=task-3="Analyze API routes"

# Apply indexed job
kubectl apply -f agent-indexed-job.yaml
```

```yaml
# agent-indexed-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: agent-batch
  namespace: warp-agents
spec:
  completions: 3
  parallelism: 3
  completionMode: Indexed
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: agent
          image: warpdotdev/dev-base:latest
          workingDir: /workspace
          command:
            - /bin/sh
            - -c
            - |
              PROMPT=$(cat /prompts/task-$JOB_COMPLETION_INDEX)
              oz agent run --prompt "$PROMPT" --share team
          env:
            - name: WARP_API_KEY
              valueFrom:
                secretKeyRef:
                  name: warp-api-key
                  key: WARP_API_KEY
          volumeMounts:
            - name: prompts
              mountPath: /prompts
            - name: workspace
              mountPath: /workspace
      volumes:
        - name: prompts
          configMap:
            name: agent-prompts
        - name: workspace
          persistentVolumeClaim:
            claimName: agent-workspace
```

### With Resource Quotas

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: agent-quota
  namespace: warp-agents
spec:
  hard:
    requests.cpu: "8"
    requests.memory: "16Gi"
    limits.cpu: "16"
    limits.memory: "32Gi"
    count/jobs.batch: "10"
```

## Monitoring

### Job Status

```bash
# List all agent jobs
kubectl get jobs -n warp-agents -l app=warp-agent

# Watch job progress
kubectl get jobs -n warp-agents -l app=warp-agent -w

# Get detailed status
kubectl describe job agent-task-1 -n warp-agents
```

### Pod Logs

```bash
# Get logs from job's pod
kubectl logs -n warp-agents -l job-name=agent-task-1 -f

# Get logs from all agents
for job in $(kubectl get jobs -n warp-agents -l app=warp-agent -o name); do
  echo "=== $job ==="
  kubectl logs -n warp-agents -l job-name=$(basename $job)
done
```

### Completion Status

```bash
# Check completion
kubectl get jobs -n warp-agents -l app=warp-agent \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.status.succeeded}/{.spec.completions}{"\n"}{end}'

# Get failed jobs
kubectl get jobs -n warp-agents -l app=warp-agent \
  --field-selector status.successful=0
```

## Result Collection

### From PVC

```bash
# Create a pod to access results
kubectl run result-collector \
  --namespace warp-agents \
  --image=busybox \
  --restart=Never \
  --overrides='
{
  "spec": {
    "containers": [{
      "name": "collector",
      "image": "busybox",
      "command": ["sleep", "3600"],
      "volumeMounts": [{
        "name": "workspace",
        "mountPath": "/workspace"
      }]
    }],
    "volumes": [{
      "name": "workspace",
      "persistentVolumeClaim": {
        "claimName": "agent-workspace"
      }
    }]
  }
}'

# Copy results locally
kubectl cp warp-agents/result-collector:/workspace/outputs ./outputs

# Clean up
kubectl delete pod result-collector -n warp-agents
```

### From Pod Logs

```bash
# Extract JSON results from logs
kubectl logs -n warp-agents -l app=warp-agent | grep "^{" | jq -s '.'
```

## Cleanup

```bash
# Delete completed jobs
kubectl delete jobs -n warp-agents -l app=warp-agent --field-selector status.successful=1

# Delete all agent jobs
kubectl delete jobs -n warp-agents -l app=warp-agent

# Full cleanup
kubectl delete namespace warp-agents
```

## Helm Chart

For production deployments, use the Helm chart:

```bash
helm install warp-agents ./helm/warp-agents \
  --namespace warp-agents \
  --create-namespace \
  --set apiKey.existingSecret=warp-api-key \
  --set tasks='{Analyze auth,Analyze API,Analyze DB}'
```

### Chart Values

```yaml
# values.yaml
image:
  repository: warpdotdev/dev-base
  tag: latest

apiKey:
  existingSecret: ""  # Use existing secret
  create: false       # Or create from value
  value: ""

tasks: []             # List of task prompts

resources:
  requests:
    memory: "2Gi"
    cpu: "1"
  limits:
    memory: "4Gi"
    cpu: "2"

persistence:
  enabled: true
  size: 10Gi
  storageClass: ""

nodeSelector: {}
tolerations: []
affinity: {}
```

## Troubleshooting

### Job Stuck in Pending

```bash
# Check pod events
kubectl describe pods -n warp-agents -l job-name=agent-task-1

# Common issues:
# - Insufficient resources: Check quota and node capacity
# - PVC not bound: Check storage class
# - Image pull errors: Check image name and registry access
```

### Job Failed

```bash
# Check exit code
kubectl get pods -n warp-agents -l job-name=agent-task-1 \
  -o jsonpath='{.items[*].status.containerStatuses[*].state.terminated.exitCode}'

# Get failure reason
kubectl describe job agent-task-1 -n warp-agents | grep -A5 "Events:"
```

### Network Issues

```bash
# Test connectivity from within cluster
kubectl run test-net --namespace warp-agents --rm -it --image=busybox -- \
  wget -qO- https://app.warp.dev/health
```
