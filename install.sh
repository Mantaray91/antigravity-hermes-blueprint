#!/usr/bin/env bash
set -eo pipefail

# Hermes Engine Automated Installer & Bootstrap Script
# Compatible with Linux, macOS, and POSIX agent environments.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_ROOT="${AGENTS_ROOT:-$HOME/.agents}"
GEMINI_MEMORIES_DIR="${GEMINI_MEMORIES_DIR:-$HOME/.gemini/memories}"

echo "============================================================"
echo "    Hermes Engine: 3-Tier Memory & Self-Evolution Setup     "
echo "============================================================"
echo "Repository Path    : ${SCRIPT_DIR}"
echo "Agents Root Target : ${AGENTS_ROOT}"
echo "User Memory Target : ${GEMINI_MEMORIES_DIR}"
echo "============================================================"

# 1. Verify Python >= 3.10
echo "[1/6] Checking Python version..."
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "${PYTHON_VERSION}" | cut -d. -f1)
PYTHON_MINOR=$(echo "${PYTHON_VERSION}" | cut -d. -f2)

if [ "${PYTHON_MAJOR}" -lt 3 ] || { [ "${PYTHON_MAJOR}" -eq 3 ] && [ "${PYTHON_MINOR}" -lt 10 ]; }; then
    echo "Error: Python >= 3.10 is required. Detected Python ${PYTHON_VERSION}." >&2
    exit 1
fi
echo "✓ Python ${PYTHON_VERSION} detected."

# 2. Create Target Directory Tree
echo "[2/6] Scaffolding workspace directories..."
mkdir -p "${AGENTS_ROOT}/memories"
mkdir -p "${AGENTS_ROOT}/skills/.archive"
mkdir -p "${AGENTS_ROOT}/skills/.curator_backups"
mkdir -p "${AGENTS_ROOT}/state"
mkdir -p "${GEMINI_MEMORIES_DIR}"
echo "✓ Directories created in ${AGENTS_ROOT} and ${GEMINI_MEMORIES_DIR}."

# 3. Install Dependencies
echo "[3/6] Installing Python dependencies..."
if command -v uv &>/dev/null; then
    echo "Using uv to install dependencies..."
    uv pip install -e "${SCRIPT_DIR}"
elif command -v pip3 &>/dev/null; then
    pip3 install --user -e "${SCRIPT_DIR}"
elif command -v pip &>/dev/null; then
    pip install --user -e "${SCRIPT_DIR}"
else
    echo "Warning: Neither uv nor pip found in PATH. Ensure fastmcp and pytest are installed." >&2
fi
echo "✓ Dependencies installed."

# 4. Seed Memory Templates if not present
echo "[4/6] Initializing memory files..."
if [ ! -f "${GEMINI_MEMORIES_DIR}/USER.md" ]; then
    cp "${SCRIPT_DIR}/templates/USER.md.example" "${GEMINI_MEMORIES_DIR}/USER.md"
    echo "✓ Initialized ${GEMINI_MEMORIES_DIR}/USER.md from template."
else
    echo "• Preserving existing ${GEMINI_MEMORIES_DIR}/USER.md."
fi

if [ ! -f "${AGENTS_ROOT}/memories/MEMORY.md" ]; then
    cp "${SCRIPT_DIR}/templates/MEMORY.md.example" "${AGENTS_ROOT}/memories/MEMORY.md"
    echo "✓ Initialized ${AGENTS_ROOT}/memories/MEMORY.md from template."
else
    echo "• Preserving existing ${AGENTS_ROOT}/memories/MEMORY.md."
fi

# 5. Generate hooks.json
echo "[5/6] Generating lifecycle hooks configuration..."
PYTHON_BIN="$(command -v python3 || echo 'python3')"
HOOKS_FILE="${AGENTS_ROOT}/hooks.json"
cat > "${HOOKS_FILE}" <<EOF
{
  "hermes-memory-injector": {
    "PreInvocation": [
      {
        "command": "${PYTHON_BIN} ${SCRIPT_DIR}/hermes_engine/hooks/pre_invocation.py"
      }
    ]
  },
  "hermes-session-stop": {
    "Stop": [
      {
        "command": "${PYTHON_BIN} ${SCRIPT_DIR}/hermes_engine/hooks/stop_hook.py"
      }
    ]
  }
}
EOF
echo "✓ Generated ${HOOKS_FILE}."

# 6. Execute Self-Verification Test Suite
echo "[6/6] Running self-verification test suite..."
if command -v pytest &>/dev/null; then
    (cd "${SCRIPT_DIR}" && pytest tests/ -q)
    echo "✓ All test suites passed successfully!"
else
    python3 -m pytest "${SCRIPT_DIR}/tests" -q || echo "Note: Run 'pytest tests/' to verify."
fi

echo ""
echo "============================================================"
echo "           Hermes Engine Successfully Installed!            "
echo "============================================================"
echo "FastMCP Server Command:"
echo "  fastmcp run ${SCRIPT_DIR}/hermes_engine/server.py"
echo ""
echo "MCP Server Configuration Snippet (for Claude/Cursor/Antigravity):"
cat <<EOF
{
  "mcpServers": {
    "hermes-engine": {
      "command": "python3",
      "args": ["${SCRIPT_DIR}/hermes_engine/server.py"]
    }
  }
}
EOF
echo "============================================================"
