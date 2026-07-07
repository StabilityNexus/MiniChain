#!/bin/sh

# Ensure we are in the root directory
cd "$(dirname "$0")/.."

HOOK_FILE=".git/hooks/pre-commit"

cat << 'EOF' > "$HOOK_FILE"
#!/bin/sh
echo "----------------------------------------"
echo "Running pytest locally before commit..."
echo "----------------------------------------"

# Run pytest using the python environment available
python -m pytest tests/ -v

# Capture the exit code of pytest
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "⚠️  WARNING: Tests failed!"
    echo "The commit will proceed, but please fix the tests ASAP."
    echo "----------------------------------------"
else
    echo ""
    echo "✅ Tests passed! Commit proceeding."
    echo "----------------------------------------"
fi

# Always exit 0 to ensure the commit is never blocked
exit 0
EOF

# Make the hook executable
chmod +x "$HOOK_FILE"

echo "✅ Pre-commit hook successfully installed at $HOOK_FILE"
