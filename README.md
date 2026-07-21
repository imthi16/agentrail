# AgentRail

**Safe, stage-based workflows for AI coding agents.**

AgentRail is a workflow control plane for coding agents such as jcode, Claude Code, Codex, Gemini CLI, and local agents. It adds explicit operating modes, approved execution stages, Git worktree isolation, tmux supervision, model profiles, checkpoints, and pull-request orchestration.

> Status: early MVP scaffold.

## Why AgentRail?

Coding agents are powerful, but many tools begin editing immediately and treat a large request as one long session. AgentRail puts the work on rails:

- Plan before editing
- Lock the allowed scope
- Split work into dependent stages
- Run each stage in an isolated workspace
- Pause for approvals at risky boundaries
- Create separate branches and pull requests
- Checkpoint, retry, resume, and roll back
- Supervise agent sessions through tmux

## Planned workflow

```text
User request
    ↓
Intent and scope contract
    ↓
Stage dependency graph
    ↓
Approval
    ↓
Worktrees + tmux sessions
    ↓
Agent execution + quality gates
    ↓
Separate draft pull requests
```

## MVP commands

```bash
agentrail init
agentrail plan "Implement login and logout as separate PRs"
agentrail status
agentrail run
agentrail pause
agentrail resume
agentrail rollback <stage-id>
```

The current scaffold implements `version`, `init`, `plan`, and `status`.

## Installation

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
agentrail --help
```

## Quick start

```bash
agentrail init
agentrail plan "Implement login and logout as separate PRs"
agentrail status
```

This creates `.agentrail/config.yaml` and `.agentrail/workflow.yaml`.

## Example workflow

```yaml
goal: Implement login and logout as separate PRs
mode: plan
status: awaiting_approval
stages:
  - id: analyse
    title: Analyse repository and authentication requirements
    depends_on: []
  - id: login
    title: Implement and validate login
    depends_on: [analyse]
  - id: logout
    title: Implement and validate logout
    depends_on: [analyse]
```

## Architecture

```text
agentrail/
├── cli.py          CLI commands
├── config.py       Project configuration
├── models.py       Workflow and stage models
└── workflow.py     Workflow creation and persistence
```

Future modules will include:

```text
adapters/           jcode, Claude Code, Codex, Gemini
policies/           modes, permissions, intent lock
workspaces/         Git worktrees, Docker, remote runners
tmux/               session and pane supervision
git/                branches, commits, stacked PRs
checkpoints/        snapshots, retries, rollback
events/             JSONL timeline and replay
```

## Roadmap

### v0.1

- [x] Python package and CLI scaffold
- [x] Project initialization
- [x] Basic workflow generation
- [ ] Approval-driven Plan/Edit/Auto modes
- [ ] Intent Lock scope contract
- [ ] Stage DAG validation
- [ ] Git worktree manager
- [ ] tmux supervisor
- [ ] Checkpoint and rollback
- [ ] Separate draft PR creation

### v0.2

- [ ] jcode adapter
- [ ] LSP diagnostics and read-before-edit guard
- [ ] Model/provider/reasoning profile picker
- [ ] Cost and retry budgets
- [ ] GitHub CI and merge gates
- [ ] Structured execution timeline

### v0.3

- [ ] Claude Code, Codex, and Gemini adapters
- [ ] React dashboard
- [ ] Remote execution
- [ ] Full workflow replay
- [ ] Full-stack runtime debugging module

## Contributing

AgentRail is at the design and MVP stage. Issues and architecture discussions are welcome.

## License

MIT
