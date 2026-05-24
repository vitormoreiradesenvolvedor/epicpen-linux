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

## Requisitos do sistema (AppImage)

| Componente | Mínimo |
|---|---|
| Distribuição Linux | Ubuntu **20.04** LTS / Debian 11 / Fedora 36+ / Arch Linux ou equivalente |
| glibc | **2.28** (requisito do PyQt6 manylinux; Ubuntu 20.04 tem 2.31 ✓) |
| Sessão gráfica | Wayland (KDE Plasma, GNOME 42+, Sway, Hyprland) ou X11 |
| Arquitectura | x86\_64 |

> `libstdc++`, `libgcc_s` e todas as libs xcb/X11 são bundladas — sem dependências de sistema além de glibc ≥ 2.28 e kernel com FUSE2 ou FUSE3.

## Stack tecnológica

- **Python 3.12** (bundlado no AppImage via python-build-standalone — sem dependências de sistema)
- **PyQt6 6.10** — interface gráfica e overlay transparente
- **AppImageTool** — empacotamento como AppImage

## Requisitos de desenvolvimento

```bash
pip install PyQt6==6.10.1
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
