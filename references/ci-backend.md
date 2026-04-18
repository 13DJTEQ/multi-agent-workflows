# CI Backend Reference

Patterns for running multi-agent workflows in CI systems.

## GitHub Actions

### Matrix Strategy

The most powerful way to parallelize agents in GitHub Actions:

```yaml
# .github/workflows/multi-agent.yml
name: Multi-Agent Analysis

on:
  workflow_dispatch:
    inputs:
      tasks:
        description: 'Comma-separated list of tasks'
        required: true
        default: 'Analyze auth,Analyze API,Analyze DB'

jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.set-matrix.outputs.matrix }}
    steps:
      - id: set-matrix
        run: |
          TASKS='${{ github.event.inputs.tasks }}'
          # Convert comma-separated to JSON array
          JSON=$(echo "$TASKS" | jq -R 'split(",") | map(ltrimstr(" "))' -c)
          echo "matrix={\"task\":$JSON}" >> $GITHUB_OUTPUT

  agent:
    needs: setup
    runs-on: ubuntu-latest
    strategy:
      matrix: ${{ fromJson(needs.setup.outputs.matrix) }}
      fail-fast: false
      max-parallel: 5
    steps:
      - uses: actions/checkout@v4

      - name: Install Oz CLI
        run: |
          curl -fsSL https://get.warp.dev/oz | bash
          echo "$HOME/.oz/bin" >> $GITHUB_PATH

      - name: Run Agent
        env:
          WARP_API_KEY: ${{ secrets.WARP_API_KEY }}
        run: |
          oz agent run \
            --prompt "${{ matrix.task }}" \
            --share team \
            --output-dir ./outputs/${{ strategy.job-index }}

      - name: Upload Results
        uses: actions/upload-artifact@v4
        with:
          name: result-${{ strategy.job-index }}
          path: ./outputs/${{ strategy.job-index }}

  aggregate:
    needs: agent
    runs-on: ubuntu-latest
    if: always()
    steps:
      - uses: actions/checkout@v4

      - name: Download All Results
        uses: actions/download-artifact@v4
        with:
          pattern: result-*
          path: ./outputs
          merge-multiple: true

      - name: Aggregate Results
        run: |
          python3 scripts/aggregate_results.py \
            --input-dir ./outputs \
            --output ./final-report.md \
            --strategy merge

      - name: Upload Final Report
        uses: actions/upload-artifact@v4
        with:
          name: final-report
          path: ./final-report.md
```

### Reusable Workflow

```yaml
# .github/workflows/agent-task.yml
name: Agent Task (Reusable)

on:
  workflow_call:
    inputs:
      prompt:
        required: true
        type: string
      output-name:
        required: true
        type: string
    secrets:
      WARP_API_KEY:
        required: true

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Oz CLI
        run: |
          curl -fsSL https://get.warp.dev/oz | bash
          echo "$HOME/.oz/bin" >> $GITHUB_PATH

      - name: Run Agent
        env:
          WARP_API_KEY: ${{ secrets.WARP_API_KEY }}
        run: |
          oz agent run --prompt "${{ inputs.prompt }}" --share team --output-dir ./output

      - name: Upload Result
        uses: actions/upload-artifact@v4
        with:
          name: ${{ inputs.output-name }}
          path: ./output
```

Usage:

```yaml
# .github/workflows/parallel-analysis.yml
name: Parallel Analysis

on: push

jobs:
  auth-analysis:
    uses: ./.github/workflows/agent-task.yml
    with:
      prompt: "Analyze authentication module for security issues"
      output-name: auth-results
    secrets:
      WARP_API_KEY: ${{ secrets.WARP_API_KEY }}

  api-analysis:
    uses: ./.github/workflows/agent-task.yml
    with:
      prompt: "Analyze API routes for performance"
      output-name: api-results
    secrets:
      WARP_API_KEY: ${{ secrets.WARP_API_KEY }}

  aggregate:
    needs: [auth-analysis, api-analysis]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - run: ls -la
```

### Self-Hosted Runners with Docker

```yaml
jobs:
  agent:
    runs-on: self-hosted
    container:
      image: warpdotdev/dev-base:latest
      env:
        WARP_API_KEY: ${{ secrets.WARP_API_KEY }}
      volumes:
        - /workspace:/workspace
    steps:
      - uses: actions/checkout@v4
      - run: oz agent run --prompt "${{ matrix.task }}" --share team
```

## Jenkins

### Declarative Pipeline

```groovy
// Jenkinsfile
pipeline {
    agent any

    environment {
        WARP_API_KEY = credentials('warp-api-key')
    }

    stages {
        stage('Parallel Agents') {
            parallel {
                stage('Auth Analysis') {
                    agent {
                        docker {
                            image 'warpdotdev/dev-base:latest'
                            args '-v ${WORKSPACE}:/workspace'
                        }
                    }
                    steps {
                        sh '''
                            oz agent run \
                                --prompt "Analyze authentication module" \
                                --share team \
                                --output-dir /workspace/outputs/auth
                        '''
                    }
                }
                stage('API Analysis') {
                    agent {
                        docker {
                            image 'warpdotdev/dev-base:latest'
                            args '-v ${WORKSPACE}:/workspace'
                        }
                    }
                    steps {
                        sh '''
                            oz agent run \
                                --prompt "Analyze API routes" \
                                --share team \
                                --output-dir /workspace/outputs/api
                        '''
                    }
                }
                stage('DB Analysis') {
                    agent {
                        docker {
                            image 'warpdotdev/dev-base:latest'
                            args '-v ${WORKSPACE}:/workspace'
                        }
                    }
                    steps {
                        sh '''
                            oz agent run \
                                --prompt "Analyze database layer" \
                                --share team \
                                --output-dir /workspace/outputs/db
                        '''
                    }
                }
            }
        }

        stage('Aggregate') {
            steps {
                sh '''
                    python3 scripts/aggregate_results.py \
                        --input-dir outputs \
                        --output final-report.md
                '''
                archiveArtifacts artifacts: 'final-report.md'
            }
        }
    }
}
```

### Scripted Pipeline with Dynamic Tasks

```groovy
// Jenkinsfile
def tasks = ['Analyze auth', 'Analyze API', 'Analyze DB', 'Analyze tests']

def parallelStages = tasks.collectEntries { task ->
    def safeName = task.replaceAll(' ', '-').toLowerCase()
    [(safeName): {
        node {
            docker.image('warpdotdev/dev-base:latest').inside("-v ${WORKSPACE}:/workspace") {
                withCredentials([string(credentialsId: 'warp-api-key', variable: 'WARP_API_KEY')]) {
                    sh """
                        oz agent run \
                            --prompt "${task}" \
                            --share team \
                            --output-dir /workspace/outputs/${safeName}
                    """
                }
            }
        }
    }]
}

pipeline {
    agent any

    stages {
        stage('Parallel Agents') {
            steps {
                script {
                    parallel parallelStages
                }
            }
        }

        stage('Aggregate') {
            steps {
                sh 'python3 scripts/aggregate_results.py --input-dir outputs --output report.md'
                archiveArtifacts 'report.md'
            }
        }
    }
}
```

## GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - analyze
  - aggregate

.agent-template:
  stage: analyze
  image: warpdotdev/dev-base:latest
  variables:
    WARP_API_KEY: $WARP_API_KEY
  script:
    - oz agent run --prompt "$TASK_PROMPT" --share team --output-dir ./output
  artifacts:
    paths:
      - output/
    expire_in: 1 hour

auth-analysis:
  extends: .agent-template
  variables:
    TASK_PROMPT: "Analyze authentication module"

api-analysis:
  extends: .agent-template
  variables:
    TASK_PROMPT: "Analyze API routes"

db-analysis:
  extends: .agent-template
  variables:
    TASK_PROMPT: "Analyze database layer"

aggregate:
  stage: aggregate
  image: python:3.11
  needs:
    - auth-analysis
    - api-analysis
    - db-analysis
  script:
    - python3 scripts/aggregate_results.py --input-dir . --output report.md
  artifacts:
    paths:
      - report.md
```

## CircleCI

```yaml
# .circleci/config.yml
version: 2.1

executors:
  agent-executor:
    docker:
      - image: warpdotdev/dev-base:latest

jobs:
  agent-task:
    executor: agent-executor
    parameters:
      task:
        type: string
      task-id:
        type: string
    steps:
      - checkout
      - run:
          name: Run Agent
          command: |
            oz agent run \
              --prompt "<< parameters.task >>" \
              --share team \
              --output-dir ./outputs/<< parameters.task-id >>
      - persist_to_workspace:
          root: .
          paths:
            - outputs/<< parameters.task-id >>

  aggregate:
    docker:
      - image: python:3.11
    steps:
      - checkout
      - attach_workspace:
          at: .
      - run:
          name: Aggregate Results
          command: |
            python3 scripts/aggregate_results.py \
              --input-dir outputs \
              --output report.md
      - store_artifacts:
          path: report.md

workflows:
  multi-agent:
    jobs:
      - agent-task:
          name: auth-analysis
          task: "Analyze authentication"
          task-id: auth
      - agent-task:
          name: api-analysis
          task: "Analyze API"
          task-id: api
      - agent-task:
          name: db-analysis
          task: "Analyze database"
          task-id: db
      - aggregate:
          requires:
            - auth-analysis
            - api-analysis
            - db-analysis
```

## Best Practices

### Secrets Management

- **Never** hardcode API keys in workflow files
- Use CI platform's secrets management (GitHub Secrets, Jenkins Credentials, etc.)
- Rotate keys regularly
- Scope secrets to specific workflows when possible

### Artifact Management

- Use unique artifact names per job
- Set appropriate retention policies
- Clean up artifacts after aggregation

### Error Handling

```yaml
# GitHub Actions - continue on failure
strategy:
  fail-fast: false  # Don't cancel other jobs if one fails

# Jenkins - catch failures
catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
    sh 'oz agent run ...'
}
```

### Cost Optimization

- Use self-hosted runners for frequent workflows
- Cache Docker images
- Set reasonable timeouts
- Use spot/preemptible instances where supported

### Audit Trail

CI runs provide automatic audit trails:
- Who triggered the workflow
- What inputs were provided
- Full logs of agent execution
- Artifact history
