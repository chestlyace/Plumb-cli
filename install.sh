#!/bin/sh
# Install Plumb on macOS or Linux:
#   curl -fsSL https://raw.githubusercontent.com/chestlyace/Plumb-cli/main/install.sh | sh
# Then run `tutor` inside a project; the first run sets up Ollama and the model.
set -eu
SOURCE="https://github.com/chestlyace/Plumb-cli/archive/refs/heads/main.zip"

echo "Installing Plumb..."
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi
uv tool install --force --refresh --python 3.14 "$SOURCE"
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "Plumb is installed."
echo "Open a new terminal, go to one of your projects, and run:  tutor"
echo "The first run sets up Ollama and the model for you."
