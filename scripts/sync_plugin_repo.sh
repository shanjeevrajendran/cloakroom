#!/usr/bin/env bash
# Copy the plugin into its own repository with the plugin at the root, for the Claude plugin directory (which needs
# hook commands without inline scripts when the plugin sits in a subfolder) and as a standalone marketplace.
#   scripts/sync_plugin_repo.sh [DEST]      default DEST: ../cloakroom-plugin
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"; DEST="${1:-$SRC/../cloakroom-plugin}"
mkdir -p "$DEST"
rm -rf "$DEST/.claude-plugin" "$DEST/hooks" "$DEST/scripts"
cp -R "$SRC/plugin/.claude-plugin" "$SRC/plugin/hooks" "$SRC/plugin/scripts" "$DEST/"
cp "$SRC/LICENSE" "$SRC/NOTICE" "$DEST/"
# This repo's README is a pointer to the main repo (so search ranks that one): short header + the install section of
# plugin/README.md (from "## Get started" up to the next "## ") + the license line.
{
  printf '%s\n\n%s\n\n' '# Cloakroom plugin (install files)' \
    'This repo only hosts the Claude Code install files. Cloakroom itself (docs, benchmarks, issues, and stars ⭐) lives at https://github.com/shanjeevrajendran/cloakroom'
  awk '/^## Get started/{p=1;print;next} p&&/^## /{exit} p' "$SRC/plugin/README.md" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
  printf '\n'
  grep -m1 '^License:' "$SRC/plugin/README.md"
} > "$DEST/README.md"
cat > "$DEST/.claude-plugin/marketplace.json" <<JSON
{
  "\$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "cloakroom-plugin",
  "description": "Cloakroom: names, emails, addresses and API keys are swapped for placeholders before Claude sees them, and restored locally.",
  "owner": {"name": "Shanjeev Rajendran", "url": "https://github.com/shanjeevrajendran"},
  "plugins": [{"name": "cloakroom", "source": "./", "category": "security",
               "tags": ["privacy", "pii", "secrets", "redaction", "security"]}]
}
JSON
find "$DEST" -name .DS_Store -delete
echo "synced plugin $(python3 -c "import json;print(json.load(open('$DEST/.claude-plugin/plugin.json'))['version'])") to $DEST"
