# Crea el repo local y lo publica en GitHub (privado) con tu cuenta.
# Requisitos: git y GitHub CLI (winget install GitHub.cli) con `gh auth login` hecho.
# Uso: abre PowerShell en esta carpeta y ejecuta  .\setup-repo.ps1
$ErrorActionPreference = "Stop"

git init -b main
git add .
git commit -m "vaultsearch: busqueda hibrida sobre la boveda de Obsidian con indexado incremental y servidor MCP"
gh repo create vault-search --private --source . --push --description "MCP server: busqueda hibrida (semantica + BM25) sobre una boveda de Obsidian, con indexado incremental automatico"

Write-Host "`nListo: $(gh repo view --json url -q .url)"
