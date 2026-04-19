#!/bin/bash
#
# Install multi-agent-workflows skill
#
# Usage:
#   ./install.sh              # Install skill only
#   ./install.sh --with-cli   # Install skill + Python CLI tools
#   ./install.sh --uninstall  # Remove installation
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="multi-agent-workflows"
SKILL_DIR="$HOME/.claude/skills/$SKILL_NAME"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

install_skill() {
    log_info "Installing $SKILL_NAME skill..."
    
    # Create skills directory if needed
    mkdir -p "$HOME/.claude/skills"
    
    # Remove existing installation
    if [ -d "$SKILL_DIR" ] || [ -L "$SKILL_DIR" ]; then
        log_warn "Removing existing installation at $SKILL_DIR"
        rm -rf "$SKILL_DIR"
    fi
    
    # Copy skill files (not symlink, so it's portable)
    cp -r "$SCRIPT_DIR" "$SKILL_DIR"
    
    # Remove dev files from installed copy
    rm -rf "$SKILL_DIR/.git"
    rm -rf "$SKILL_DIR/.github"
    rm -f "$SKILL_DIR/.gitignore"
    rm -rf "$SKILL_DIR/.pytest_cache"
    find "$SKILL_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$SKILL_DIR" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    
    log_info "Skill installed to $SKILL_DIR"
}

install_cli_pipx() {
    log_info "Installing CLI via pipx (isolated env)..."
    pipx install "$SCRIPT_DIR" --force --quiet
    return $?
}

install_cli_venv() {
    log_info "Installing CLI via dedicated venv + symlinks..."
    
    local venv_dir="$SKILL_DIR/.venv"
    local bin_dir="$HOME/.local/bin"
    
    # Create venv
    python3 -m venv "$venv_dir"
    
    # Install package into venv
    "$venv_dir/bin/pip" install -e "$SKILL_DIR" --quiet
    
    # Symlink CLIs into ~/.local/bin (widely on PATH)
    mkdir -p "$bin_dir"
    for cmd in maw-spawn-docker maw-spawn-k8s maw-wait-phase maw-aggregate maw-eval; do
        ln -sf "$venv_dir/bin/$cmd" "$bin_dir/$cmd"
    done
    
    # Warn if bin_dir not on PATH
    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
        log_warn "$bin_dir is not on your PATH."
        log_warn "Add to your shell rc: export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
    
    return 0
}

install_cli() {
    log_info "Installing CLI tools..."
    
    # Check for python3
    if ! command -v python3 &> /dev/null; then
        log_error "python3 not found. Please install Python 3.10+ first."
        exit 1
    fi
    
    # Prefer pipx (cleanest), fall back to venv + symlinks
    if command -v pipx &> /dev/null; then
        install_cli_pipx
    else
        log_warn "pipx not found. Using venv fallback."
        log_warn "(Install pipx for cleaner management: brew install pipx)"
        install_cli_venv
    fi
    
    log_info "CLI tools installed:"
    log_info "  maw-spawn-docker - Spawn parallel Docker agents"
    log_info "  maw-spawn-k8s    - Spawn K8s Job agents"
    log_info "  maw-wait-phase   - Wait for phase completion"
    log_info "  maw-aggregate    - Aggregate agent results"
    log_info "  maw-eval         - Run skill evaluations"
}

uninstall() {
    log_info "Uninstalling $SKILL_NAME..."
    
    # Remove pipx installation
    if command -v pipx &> /dev/null && pipx list 2>/dev/null | grep -q "$SKILL_NAME"; then
        pipx uninstall "$SKILL_NAME" --quiet 2>/dev/null || true
        log_info "Removed pipx package"
    fi
    
    # Remove venv symlinks
    for cmd in maw-spawn-docker maw-spawn-k8s maw-wait-phase maw-aggregate maw-eval; do
        if [ -L "$HOME/.local/bin/$cmd" ]; then
            rm -f "$HOME/.local/bin/$cmd"
        fi
    done
    
    # Remove skill directory (includes venv)
    if [ -d "$SKILL_DIR" ] || [ -L "$SKILL_DIR" ]; then
        rm -rf "$SKILL_DIR"
        log_info "Removed $SKILL_DIR"
    fi
    
    log_info "Uninstall complete"
}

verify_installation() {
    log_info "Verifying installation..."
    
    # Check skill directory
    if [ -d "$SKILL_DIR" ]; then
        log_info "✓ Skill directory exists"
    else
        log_error "✗ Skill directory not found"
        return 1
    fi
    
    # Check SKILL.md
    if [ -f "$SKILL_DIR/SKILL.md" ]; then
        log_info "✓ SKILL.md present"
    else
        log_error "✗ SKILL.md not found"
        return 1
    fi
    
    # Check scripts
    if [ -d "$SKILL_DIR/scripts" ]; then
        log_info "✓ Scripts directory present"
    else
        log_error "✗ Scripts directory not found"
        return 1
    fi
    
    # Check CLI if installed
    if command -v maw-spawn-docker &> /dev/null; then
        log_info "✓ CLI tools available"
    else
        log_warn "CLI tools not installed (use --with-cli to install)"
    fi
    
    log_info "Installation verified successfully"
}

show_help() {
    cat << EOF
Multi-Agent Workflows Installer

Usage: ./install.sh [OPTIONS]

Options:
  --with-cli    Install Python CLI tools (maw-* commands)
  --uninstall   Remove all installed components
  --verify      Verify existing installation
  --help        Show this help message

The skill will be installed to: $SKILL_DIR

After installation, the skill can be invoked via:
  - Natural language triggers like "parallelize this task"
  - Direct reference to multi-agent-workflows patterns
EOF
}

# Parse arguments
WITH_CLI=false
UNINSTALL=false
VERIFY=false

for arg in "$@"; do
    case $arg in
        --with-cli)
            WITH_CLI=true
            ;;
        --uninstall)
            UNINSTALL=true
            ;;
        --verify)
            VERIFY=true
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $arg"
            show_help
            exit 1
            ;;
    esac
done

# Execute
if $UNINSTALL; then
    uninstall
elif $VERIFY; then
    verify_installation
else
    install_skill
    if $WITH_CLI; then
        install_cli
    fi
    verify_installation
fi

log_info "Done!"
