#!/bin/bash

# PAL MCP Server - Code Quality Checks
# This script runs all required linting and testing checks before committing changes.
# ALL checks must pass 100% for CI/CD to succeed.
#
# Usage:
#   ./code_quality_checks.sh          Fix lint and formatting in place, then run the unit tests.
#   ./code_quality_checks.sh --check  Change no files. Fail if ruff, black or isort finds a problem
#                                     (GitHub Actions runs this mode).

set -e  # Exit on any error

CHECK_ONLY=false
case "${1:-}" in
    "") ;;
    --check) CHECK_ONLY=true ;;
    *) echo "Usage: $0 [--check]"; exit 2 ;;
esac

echo "🔍 Running Code Quality Checks for PAL MCP Server"
echo "================================================="

# Determine Python command. The checks run in the dev venv (.dev_venv). They never use or change the runtime
# venv (.pal_venv) that the MCP server runs on, so that it holds only requirements.txt.
DEV_VENV=".dev_venv"
dev_install() {
    if command -v uv &> /dev/null; then
        uv pip install -q --python "$DEV_VENV/bin/python" "$@"
    else
        # A venv that uv made has no pip.
        "$DEV_VENV/bin/python" -m pip --version &> /dev/null || "$DEV_VENV/bin/python" -m ensurepip -q
        "$DEV_VENV/bin/python" -m pip install -q "$@"
    fi
}
if [[ -f "$DEV_VENV/bin/python" ]]; then
    PYTHON_CMD="$DEV_VENV/bin/python"
    PIP_CMD="dev_install"
    # Keep the dev venv in step with both requirements files; this is quick when nothing changed.
    $PIP_CMD -r requirements.txt -r requirements-dev.txt
    echo "✅ Using the dev venv: $DEV_VENV"
elif [[ "${CI:-}" == "true" ]]; then
    # CI runners (GitHub Actions sets CI=true) install the dependencies into the job's own Python.
    PYTHON_CMD="python"
    PIP_CMD="python -m pip install -q"
    echo "✅ Using the CI Python: $(python --version)"
else
    echo "📦 Creating the dev venv $DEV_VENV (requirements.txt and requirements-dev.txt)..."
    # Test on the Python version that the server runs on, when the runtime venv exists.
    RUNTIME_VERSION=""
    if [[ -x ".pal_venv/bin/python" ]]; then
        RUNTIME_VERSION=$(.pal_venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
    fi
    if command -v uv &> /dev/null; then
        uv venv -q ${RUNTIME_VERSION:+--python "$RUNTIME_VERSION"} "$DEV_VENV"
    elif [[ -n "$RUNTIME_VERSION" ]] && command -v "python$RUNTIME_VERSION" &> /dev/null; then
        "python$RUNTIME_VERSION" -m venv "$DEV_VENV"
    else
        python3 -m venv "$DEV_VENV"
    fi
    PYTHON_CMD="$DEV_VENV/bin/python"
    PIP_CMD="dev_install"
    $PIP_CMD -r requirements.txt -r requirements-dev.txt
    echo "✅ Created the dev venv: $DEV_VENV"
fi
echo ""

# Check and install dev dependencies if needed
echo "🔍 Checking development dependencies..."
DEV_DEPS_NEEDED=false

# Check each dev dependency
for tool in ruff black isort pytest; do
    # Check if tool exists in venv or in PATH
    if [[ -f "$DEV_VENV/bin/$tool" ]] || { [[ "$PYTHON_CMD" == "python" ]] && command -v $tool &> /dev/null; }; then
        continue
    else
        DEV_DEPS_NEEDED=true
        break
    fi
done

if [ "$DEV_DEPS_NEEDED" = true ]; then
    echo "📦 Installing development dependencies..."
    $PIP_CMD -r requirements-dev.txt
    echo "✅ Development dependencies installed"
else
    echo "✅ Development dependencies already installed"
fi

# Set tool paths
if [[ -f "$DEV_VENV/bin/ruff" ]]; then
    RUFF="$DEV_VENV/bin/ruff"
    BLACK="$DEV_VENV/bin/black"
    ISORT="$DEV_VENV/bin/isort"
    PYTEST="$DEV_VENV/bin/pytest"
else
    RUFF="ruff"
    BLACK="black"
    ISORT="isort"
    PYTEST="pytest"
fi
echo ""

# Step 1: Linting and Formatting
echo "📋 Step 1: Running Linting and Formatting Checks"
echo "--------------------------------------------------"

if [ "$CHECK_ONLY" = true ]; then
    echo "🔧 Running ruff linting (check only)..."
    $RUFF check --exclude test_simulation_files --exclude .pal_venv --exclude .dev_venv

    echo "🎨 Checking black code formatting..."
    $BLACK --check --diff . --exclude="(test_simulation_files/|\.pal_venv/|\.dev_venv/)"

    echo "📦 Checking import sorting with isort..."
    $ISORT --check-only --diff . --skip-glob=".pal_venv/*" --skip-glob=".dev_venv/*" --skip-glob="test_simulation_files/*"
else
    echo "🔧 Running ruff linting with auto-fix..."
    $RUFF check --fix --exclude test_simulation_files --exclude .pal_venv --exclude .dev_venv

    echo "🎨 Running black code formatting..."
    $BLACK . --exclude="(test_simulation_files/|\.pal_venv/|\.dev_venv/)"

    echo "📦 Running import sorting with isort..."
    $ISORT . --skip-glob=".pal_venv/*" --skip-glob=".dev_venv/*" --skip-glob="test_simulation_files/*"

    echo "✅ Verifying all linting passes..."
    $RUFF check --exclude test_simulation_files --exclude .pal_venv --exclude .dev_venv
fi

echo "✅ Step 1 Complete: All linting and formatting checks passed!"
echo ""

# Step 2: Unit Tests
echo "🧪 Step 2: Running Complete Unit Test Suite"
echo "---------------------------------------------"

echo "🏃 Running unit tests (excluding integration tests)..."
$PYTHON_CMD -m pytest tests/ -v -x -m "not integration"

echo "✅ Step 2 Complete: All unit tests passed!"
echo ""

# Step 3: Final Summary
echo "🎉 All Code Quality Checks Passed!"
echo "=================================="
echo "✅ Linting (ruff): PASSED"
echo "✅ Formatting (black): PASSED" 
echo "✅ Import sorting (isort): PASSED"
echo "✅ Unit tests: PASSED"
echo ""
echo "🚀 Your code is ready for commit and GitHub Actions!"
echo "💡 Remember to add simulator tests if you modified tools"