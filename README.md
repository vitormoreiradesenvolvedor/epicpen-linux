# EpicPen Linux

Reimplementação open-source da ferramenta [EpicPen](https://epic-pen.com/) para Linux, distribuída como AppImage.

## Funcionalidades planejadas

- Desenho sobre a tela com caneta, marcador e formas
- Múltiplas cores e tamanhos de pincel
- Desfazer / Refazer (Ctrl+Z / Ctrl+Y)
- Limpar tela
- Modo quadro branco
- Ponteiro laser
- Lupa / Zoom
- Painel flutuante sempre no topo
- Suporte a múltiplos monitores

## Stack tecnológica

- **Python 3.11+**
- **PyQt6** — interface gráfica e overlay transparente
- **AppImageTool** — empacotamento como AppImage

## Requisitos de desenvolvimento

```bash
pip install PyQt6
```

## Executar em desenvolvimento

```bash
python src/main.py
```

## Build AppImage

```bash
bash scripts/build_appimage.sh
```

## Fluxo de branches

```
master (protegida)
  └── development
        ├── feature/nome-da-feature
        ├── fix/nome-do-bug
        └── chore/tarefa
```

**Regras:**
- `master` aceita merge **somente** de `development`
- Commits diretos em `master` são **bloqueados**
- Novas branches devem ser criadas **a partir de `development`**
- Pull Requests para `master` de qualquer branch que não seja `development` são **bloqueados**

## Contribuindo

Leia [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) antes de abrir PRs.
teste
