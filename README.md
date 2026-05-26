# EpicPen Linux

Reimplementação open-source da ferramenta [EpicPen](https://epic-pen.com/) para Linux, distribuída como AppImage autocontido — sem instalação, sem dependências de sistema.

O EpicPen é uma ferramenta amplamente usada no Windows para desenhar, anotar e destacar conteúdo diretamente sobre o ecrã durante apresentações, aulas e reuniões. Esta implementação traz as mesmas capacidades para Linux com suporte nativo a Wayland e X11.

## Funcionalidades

- Desenho sobre o ecrã com caneta, marcador e formas geométricas
- Múltiplas cores e tamanhos de pincel
- Desfazer / Refazer (Ctrl+Z / Ctrl+Y)
- Limpar ecrã
- Modo quadro branco
- Ponteiro laser
- Lupa / Zoom
- Toolbar flutuante colapsável, sempre no topo
- Suporte a múltiplos monitores
- Suporte nativo a Wayland (wlr-layer-shell) e X11

## Download

Vá à página de [Releases](../../releases) e descarregue o AppImage mais recente.

```bash
chmod +x EpicPen-v*.AppImage
./EpicPen-v*.AppImage
```

Não é necessário instalar nada. O AppImage inclui Python 3.12, PyQt6 e todas as bibliotecas nativas.

## Requisitos do sistema

| Componente | Mínimo |
|---|---|
| Distribuição | Ubuntu **22.04** LTS / Debian 12 / Fedora 38+ / Arch Linux / Manjaro ou equivalente |
| glibc | **2.34**+ (Ubuntu 22.04 ✓) |
| Sessão gráfica | Wayland (GNOME 42+, KDE Plasma 5.27+) ou X11 |
| FUSE | FUSE2 **ou** FUSE3 (a maioria das distros já inclui um dos dois) |
| Arquitectura | x86\_64 |

> `libstdc++`, `libgcc_s`, `libxkbcommon` e todas as libs xcb/X11 estão bundladas no AppImage — sem dependências de sistema além de glibc e FUSE.

## Stack tecnológica

| Componente | Versão | Papel |
|---|---|---|
| Python | 3.12 (bundlado via python-build-standalone) | Runtime — sem dependência do Python do sistema |
| PyQt6 | 6.10.1 | Interface gráfica, overlay transparente, eventos de input |
| AppImageTool | build 295 | Empacotamento como AppImage portátil |
| type2-runtime | fuse3 (old release) | Runtime AppImage com suporte a FUSE2 e FUSE3 |

## Estrutura do projeto

```
epicpen-linux/
├── src/                        # Código-fonte Python
│   ├── main.py                 # Ponto de entrada — inicializa app, toolbar, overlay, tray
│   ├── overlay.py              # Janela transparente de desenho (canvas principal)
│   ├── toolbar.py              # Toolbar flutuante colapsável (window Wayland layer-shell)
│   ├── icons.py                # Todos os ícones desenhados com QPainter (sem imagens externas)
│   ├── tray.py                 # Ícone na system tray e menu de contexto
│   ├── config.py               # Persistência de configurações em ~/.config/epicpen/config.json
│   ├── layershell.py           # Interface ctypes para libLayerShellQtInterface (Wayland)
│   ├── keepabove.py            # keepAbove via KWin DBus scripting (KDE Plasma)
│   ├── magnifier.py            # Lupa circular (15fps Wayland, 60fps X11)
│   ├── screenshot.py           # Captura de ecrã (grim → gnome-screenshot → spectacle → scrot)
│   ├── cursors.py              # Cursores personalizados (caneta, borracha, crosshair)
│   └── hotkeys.py              # Atalhos de teclado globais
├── scripts/
│   ├── build_appimage.sh       # Build do AppImage para desenvolvimento/testes
│   ├── release.sh              # Gerador interactivo de releases (cria tag + AppImage)
│   └── generate_icon.py        # Gera resources/icons/epicpen.png 256×256 via QPainter
├── resources/
│   └── icons/                  # Ícones gerados (epicpen.png)
├── tests/                      # Testes unitários (pytest)
├── docs/
│   └── CONTRIBUTING.md         # Guia de contribuição
├── tools/                      # Cache local (appimagetool, Python standalone, libs Ubuntu)
├── lib/                        # libLayerShellQtInterface.so.6 (para Wayland layer-shell)
├── AppDir/                     # Diretório de build do AppImage (gerado automaticamente)
├── run.sh                      # Script de execução rápida para desenvolvimento
└── epicpen.desktop             # Ficheiro .desktop para integração no sistema
```

### Suporte a compositors

| Compositor | Estado | Notas |
|---|---|---|
| KDE Plasma (KWin) | ✅ Suportado | layer-shell nativo; overlay e toolbar como superfícies separadas |
| GNOME (Mutter) | ✅ Suportado | corre via XWayland; `_NET_WM_STATE_ABOVE` garante always-on-top |
| X11 genérico | ✅ Suportado | `WindowStaysOnTopHint` + keepAbove via KWin DBus |
| Hyprland | ⚠️ Não suportado oficialmente | não entrega `wl_pointer.motion` durante implicit grab em superfícies layer-shell, impedindo o desenho e o drag. Contribuições são bem-vindas — veja [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) |
| niri e outros compositors wlr | ⚠️ Não suportado oficialmente | pode funcionar parcialmente; sem testes regulares. Contribuições são bem-vindas |

### Arquitetura da janela

O EpicPen Linux usa **duas janelas independentes** em Wayland com layer-shell:

- `OverlayWindow` — superfície layer-shell `Layer::Top`, 4-anchor, cobre o ecrã inteiro; recebe os eventos de desenho
- `ToolbarWindow` — superfície layer-shell `Layer::Top` separada; flutua sobre o overlay

Em GNOME (XWayland) e X11, ambas as janelas usam `WindowStaysOnTopHint` para permanecerem acima de outras janelas.

## Desenvolvimento

### Requisitos

```bash
pip install PyQt6==6.10.1
```

É necessário ter `libLayerShellQtInterface.so.6` instalada para suporte a Wayland. Em Fedora/KDE:

```bash
sudo dnf install kf6-layer-shell-qt
```

### Executar sem build

```bash
bash run.sh
```

O `run.sh` executa `python3 src/main.py` sem sobrescrever variáveis de ambiente como `QT_QPA_PLATFORM`, permitindo testar tanto em Wayland como em X11 conforme a sessão ativa.

### Executar diretamente

```bash
python3 src/main.py
```

### Testes

```bash
pytest tests/
```

Os testes usam stubs Qt sem display real — podem correr em ambientes CI sem servidor gráfico.

### Build do AppImage

```bash
bash scripts/build_appimage.sh
```

O script descarrega automaticamente o Python standalone, instala o PyQt6, bundla todas as libs nativas (xcb, xkbcommon, libstdc++, etc.) e gera o AppImage pronto a distribuir. O resultado fica em `EpicPen-<versão>-x86_64.AppImage` na raiz do projeto.

Na primeira execução faz o download de ~300MB (Python standalone + PyQt6). Nas seguintes usa o cache em `tools/`.

### Gerar um release

```bash
bash scripts/release.sh
```

O `release.sh` é um gerador interactivo que:
1. Pergunta se é versão oficial ou de desenvolvimento
2. Sugere a próxima versão com base na última tag git
3. Cria a tag git (apenas em versão oficial)
4. Faz o build do AppImage com o número de versão correto

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
- Mensagens de commit seguem o formato [Conventional Commits](https://www.conventionalcommits.org/)

## Landing Page

https://vitormoreiradesenvolvedor.github.io/epicpen-linux


## Contribuindo

Leia [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) antes de abrir PRs.
