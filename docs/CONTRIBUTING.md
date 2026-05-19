# Guia de Contribuição

## Modelo de branching

Este projeto usa um modelo de branches rígido para manter a estabilidade da `master`.

### Estrutura

```
master        — produção, sempre estável, somente recebe merge de development
development   — integração, ponto de partida para todas as novas branches
```

### Criando uma branch de trabalho

**Sempre** crie branches a partir de `development`:

```bash
git checkout development
git pull origin development
git checkout -b feature/minha-feature
```

Prefixos aceitos:
- `feature/` — nova funcionalidade
- `fix/`     — correção de bug
- `chore/`   — tarefas de manutenção, CI, docs
- `refactor/`— refatoração sem mudança de comportamento
- `test/`    — adição/ajuste de testes

### Abrindo Pull Request

- O PR deve ser de `feature/*` ou `fix/*` → **`development`**
- Somente `development` → **`master`** é permitido
- PRs diretos de qualquer outra branch para `master` serão bloqueados pelos hooks

### Formato de commit

```
tipo(escopo): descrição curta em imperativo

Corpo opcional explicando o porquê, não o quê.
```

Exemplos:
```
feat(overlay): add transparent drawing layer
fix(toolbar): correct color picker alignment
chore(build): update appimage build script
```
