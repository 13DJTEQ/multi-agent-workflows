# Remote/SSH Backend Reference

Patterns for running multi-agent workflows on remote machines via SSH.

## Prerequisites

- SSH access to remote hosts (key-based auth recommended)
- Oz CLI installed on remote hosts, or ability to install
- `WARP_API_KEY` available on remote hosts

## Initial Setup

### Remote Host Preparation

```bash
# On each remote host, install Oz CLI
curl -fsSL https://get.warp.dev/oz | bash

# Add to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.oz/bin:$PATH"

# Verify installation
oz --version
```

### SSH Key Setup

```bash
# Generate key if needed
ssh-keygen -t ed25519 -f ~/.ssh/warp-agents -N ""

# Copy to remote hosts
for host in host1 host2 host3; do
  ssh-copy-id -i ~/.ssh/warp-agents.pub $host
done

# Test connectivity
for host in host1 host2 host3; do
  ssh -i ~/.ssh/warp-agents $host "echo 'Connected to' \$(hostname)"
done
```

### API Key Distribution

```bash
# Option 1: Pass as environment variable (per-session)
ssh $host "export WARP_API_KEY='$WARP_API_KEY' && oz agent run ..."

# Option 2: Store in remote ~/.bashrc (persistent)
ssh $host "echo 'export WARP_API_KEY=\"$WARP_API_KEY\"' >> ~/.bashrc"

# Option 3: Use ssh-agent forwarding with secrets manager
# (Most secure for production)
```

## Spawning Patterns

### Basic Parallel Spawn

```bash
#!/bin/bash
# spawn_remote.sh

HOSTS=("host1" "host2" "host3")
TASKS=(
  "Analyze authentication module"
  "Analyze API routes"
  "Analyze database layer"
)

# Spawn agents in parallel
for i in "${!HOSTS[@]}"; do
  host="${HOSTS[$i]}"
  task="${TASKS[$i]}"
  
  echo "Spawning agent on $host: $task"
  ssh "$host" "
    export WARP_API_KEY='$WARP_API_KEY'
    cd /workspace
    oz agent run --prompt '$task' --share team --output-dir /tmp/agent-output
  " &
done

# Wait for all to complete
wait
echo "All agents completed"
```

### With Workspace Sync

```bash
#!/bin/bash
# sync_and_spawn.sh

HOSTS=("host1" "host2" "host3")
WORKSPACE="/path/to/local/workspace"
REMOTE_WORKSPACE="/home/user/workspace"

# Sync workspace to all hosts
for host in "${HOSTS[@]}"; do
  echo "Syncing workspace to $host..."
  rsync -avz --delete \
    --exclude '.git' \
    --exclude 'node_modules' \
    --exclude '__pycache__' \
    "$WORKSPACE/" "$host:$REMOTE_WORKSPACE/" &
done
wait

# Run agents
for i in "${!HOSTS[@]}"; do
  host="${HOSTS[$i]}"
  task="${TASKS[$i]}"
  
  ssh "$host" "
    export WARP_API_KEY='$WARP_API_KEY'
    cd $REMOTE_WORKSPACE
    oz agent run --prompt '$task' --share team
  " &
done
wait
```

### With GNU Parallel

```bash
# Define hosts and tasks
echo "host1:Analyze auth
host2:Analyze API
host3:Analyze DB" > tasks.txt

# Run with GNU parallel
cat tasks.txt | parallel --colsep ':' \
  ssh {1} "export WARP_API_KEY='$WARP_API_KEY' && \
           cd /workspace && \
           oz agent run --prompt '{2}' --share team"
```

### With tmux Sessions

```bash
#!/bin/bash
# spawn_with_tmux.sh

for host in host1 host2 host3; do
  ssh "$host" "
    tmux new-session -d -s agent-session '
      export WARP_API_KEY=\"$WARP_API_KEY\"
      cd /workspace
      oz agent run --prompt \"$task\" --share team
      echo \"Agent completed. Press Enter to close.\"
      read
    '
  "
done

# Attach to monitor
ssh host1 "tmux attach -t agent-session"
```

## Monitoring

### Check Agent Status

```bash
# Check if agent process is running
for host in host1 host2 host3; do
  echo "=== $host ==="
  ssh "$host" "pgrep -af 'oz agent' || echo 'No agent running'"
done
```

### Stream Logs

```bash
# Tail logs from all hosts
for host in host1 host2 host3; do
  ssh "$host" "tail -f /tmp/agent-output/agent.log" | sed "s/^/[$host] /" &
done
wait
```

### Check tmux Sessions

```bash
for host in host1 host2 host3; do
  echo "=== $host ==="
  ssh "$host" "tmux list-sessions 2>/dev/null || echo 'No tmux sessions'"
done
```

## Result Collection

### Rsync Results Back

```bash
#!/bin/bash
# collect_results.sh

HOSTS=("host1" "host2" "host3")
REMOTE_OUTPUT="/tmp/agent-output"
LOCAL_OUTPUT="./outputs"

mkdir -p "$LOCAL_OUTPUT"

for host in "${HOSTS[@]}"; do
  echo "Collecting results from $host..."
  rsync -avz "$host:$REMOTE_OUTPUT/" "$LOCAL_OUTPUT/$host/" &
done
wait

echo "Results collected to $LOCAL_OUTPUT"
ls -la "$LOCAL_OUTPUT"
```

### SCP Individual Files

```bash
for host in host1 host2 host3; do
  scp "$host:/tmp/agent-output/result.json" "./outputs/$host-result.json"
done

# Combine results
jq -s '.' ./outputs/*-result.json > combined.json
```

### Direct Output Capture

```bash
# Capture output directly from SSH
for host in host1 host2 host3; do
  ssh "$host" "cat /tmp/agent-output/result.json" > "./outputs/$host.json"
done
```

## Cleanup

### Kill Running Agents

```bash
for host in host1 host2 host3; do
  echo "Stopping agents on $host..."
  ssh "$host" "pkill -f 'oz agent' || true"
done
```

### Clean Remote Outputs

```bash
for host in host1 host2 host3; do
  ssh "$host" "rm -rf /tmp/agent-output/*"
done
```

### Kill tmux Sessions

```bash
for host in host1 host2 host3; do
  ssh "$host" "tmux kill-session -t agent-session 2>/dev/null || true"
done
```

## Advanced Patterns

### With Ansible

```yaml
# playbook.yml
---
- name: Run Multi-Agent Workflow
  hosts: agent_hosts
  vars:
    warp_api_key: "{{ lookup('env', 'WARP_API_KEY') }}"
  tasks:
    - name: Sync workspace
      synchronize:
        src: /local/workspace/
        dest: /home/{{ ansible_user }}/workspace/
        delete: yes
        rsync_opts:
          - "--exclude=.git"
          - "--exclude=node_modules"

    - name: Run agent task
      shell: |
        export WARP_API_KEY="{{ warp_api_key }}"
        cd /home/{{ ansible_user }}/workspace
        oz agent run --prompt "{{ task_prompt }}" --share team --output-dir /tmp/output
      async: 3600
      poll: 30
      environment:
        WARP_API_KEY: "{{ warp_api_key }}"

    - name: Fetch results
      fetch:
        src: /tmp/output/result.json
        dest: ./outputs/{{ inventory_hostname }}/
        flat: yes
```

Inventory:

```ini
# hosts.ini
[agent_hosts]
host1 task_prompt="Analyze authentication"
host2 task_prompt="Analyze API routes"
host3 task_prompt="Analyze database"
```

Run:

```bash
ansible-playbook -i hosts.ini playbook.yml
```

### With Fabric (Python)

```python
# fabfile.py
from fabric import Connection, ThreadingGroup

HOSTS = ['host1', 'host2', 'host3']
TASKS = [
    'Analyze authentication module',
    'Analyze API routes',
    'Analyze database layer'
]

def run_agents():
    """Run agents on all hosts in parallel."""
    import os
    api_key = os.environ['WARP_API_KEY']
    
    for host, task in zip(HOSTS, TASKS):
        conn = Connection(host)
        conn.run(f'''
            export WARP_API_KEY="{api_key}"
            cd /workspace
            oz agent run --prompt "{task}" --share team --output-dir /tmp/output
        ''', asynchronous=True)

def collect_results():
    """Collect results from all hosts."""
    import os
    os.makedirs('outputs', exist_ok=True)
    
    for host in HOSTS:
        conn = Connection(host)
        conn.get('/tmp/output/result.json', f'outputs/{host}.json')

def cleanup():
    """Clean up remote outputs."""
    group = ThreadingGroup(*HOSTS)
    group.run('rm -rf /tmp/output/*')
```

Run:

```bash
fab run-agents
fab collect-results
fab cleanup
```

### Jump Host / Bastion

```bash
# SSH config for jump host
# ~/.ssh/config
Host bastion
    HostName bastion.example.com
    User admin
    IdentityFile ~/.ssh/warp-agents

Host internal-*
    ProxyJump bastion
    User agent-user
    IdentityFile ~/.ssh/warp-agents

Host internal-1
    HostName 10.0.1.10

Host internal-2
    HostName 10.0.1.11

Host internal-3
    HostName 10.0.1.12
```

```bash
# Now spawn through bastion automatically
for host in internal-1 internal-2 internal-3; do
  ssh "$host" "oz agent run --prompt '$task' --share team" &
done
wait
```

## Troubleshooting

### SSH Connection Issues

```bash
# Test connectivity with verbose output
ssh -vvv host1 "echo test"

# Check SSH agent
ssh-add -l

# Test specific key
ssh -i ~/.ssh/warp-agents host1 "echo test"
```

### Agent Not Found

```bash
# Check if oz is in PATH on remote
ssh host1 "which oz || echo 'oz not found'"

# Check installation
ssh host1 "ls -la ~/.oz/bin/"

# Install if missing
ssh host1 "curl -fsSL https://get.warp.dev/oz | bash"
```

### API Key Issues

```bash
# Verify API key is set
ssh host1 "echo \$WARP_API_KEY | head -c 10"

# Test authentication
ssh host1 "oz auth status"
```

### Permission Denied on Output

```bash
# Check output directory permissions
ssh host1 "ls -la /tmp/agent-output"

# Create with correct permissions
ssh host1 "mkdir -p /tmp/agent-output && chmod 755 /tmp/agent-output"
```
