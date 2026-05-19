#!/usr/bin/env bash
# Configura o ambiente de desenvolvimento local e ativa os git hooks.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "==> Configurando hooks git..."
git -C "$PROJECT_ROOT" config core.hooksPath .githooks
echo "    Hooks ativados em .githooks/"

echo "==> Criando branch development (se não existir)..."
cd "$PROJECT_ROOT"
if ! git show-ref --verify --quiet refs/heads/development; then
    git checkout -b development
    echo "    Branch 'development' criada."
else
    echo "    Branch 'development' já existe."
fi

echo "==> Instalando dependências Python..."
if command -v pip3 &>/dev/null; then
    pip3 install --user PyQt6
    echo "    PyQt6 instalado."
else
    echo "AVISO: pip3 não encontrado. Instale Python 3.11+ e execute:"
    echo "  pip3 install PyQt6"
fi

echo ""
echo "Setup concluído. Fluxo de trabalho:"
echo "  git checkout development"
echo "  git checkout -b feature/sua-feature"
echo "  # ... edite o código ..."
echo "  git add . && git commit -m 'feat(escopo): descrição'"
