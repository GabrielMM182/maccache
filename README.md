# maccache

Auditor e limpador de cache do macOS, feito para rodar no terminal — **sem frontend, sem dependências externas**.

Ele responde três perguntas:

1. O que está ocupando espaço no meu disco?
2. Disso, o que é cache de verdade?
3. Posso apagar com segurança — e com um comando só?

## Local do projeto neste Mac

```
/Users/gabriel/Documents/Projeto Padrão
```

> O nome da pasta tem um espaço (`Projeto Padrão`). No terminal, use aspas ou escape:
> `cd ~/Documents/Projeto\ Padrão` ou `cd "~/Documents/Projeto Padrão"`.

Arquivos:

```
Projeto Padrão/
├── maccache.py   # CLI completo (scan + clean)
├── README.md
└── .gitignore
```

## Requisitos

- macOS
- Python 3.9+ (já vem instalado no sistema)

## Instalação

Não precisa instalar nada. O script já é executável:

```bash
cd ~/Documents/Projeto\ Padrão
chmod +x maccache.py      # só na primeira vez, se necessário
./maccache.py scan
```

Alternativa equivalente, sem depender do bit de execução:

```bash
python3 maccache.py scan
```

## Uso rápido

```bash
# 1. Só olhar (não apaga nada)
./maccache.py scan

# 2. Ver tudo, inclusive itens pequenos
./maccache.py scan --min-size 0

# 3. Simular a limpeza
./maccache.py clean --dry-run

# 4. Limpar de verdade, escolhendo item por item
./maccache.py clean

# 5. Incluir também os itens de "cuidado" na lista
./maccache.py clean --category tudo
```

Saída em JSON, para scripts (o `jq` é opcional):

```bash
./maccache.py scan --json
./maccache.py scan --json | jq '.[] | select(.category=="seguro")'
```

## Categorias

| Categoria | Significado | A ferramenta apaga? |
|---|---|---|
| `seguro` | Cache puro. O app recria sozinho. | Sim, com confirmação |
| `cuidado` | Apagável, mas pode custar tempo, reconfiguração ou dados offline (ex.: `Service Worker`, `IndexedDB`, simuladores, Docker) | Só com `--category tudo` |
| `protegido` | Arquivos pessoais e do sistema (Desktop, Documents, Keychains, Mail, swap…) | **Nunca** |

## Como ele decide o que é cache

A ferramenta **não varre o disco inteiro** procurando pastas chamadas "cache". Ela usa um catálogo de locais conhecidos do macOS e classifica cada um:

| Local verificado | Categoria | Por quê |
|---|---|---|
| `~/Library/Caches/*` | `seguro` | Por definição, cache de apps; recriado sozinho |
| `~/Library/Caches/Homebrew` | `seguro` | Pacotes baixados pelo `brew` |
| `~/Library/Caches/*ShipIt`, `*updater*`, `*Sparkle*` | `seguro` | Instaladores de atualização já aplicados |
| `~/Library/Application Support/<App>/**/Cache`, `Code Cache`, `CachedData`, `CachedExtensionVSIXs`, `GPUCache` | `seguro` | Caches internos de apps Electron/Chromium |
| `~/Library/Application Support/stremio-server/stremio-cache` | `seguro` | Cache de vídeo do Stremio |
| `~/Library/Logs` | `seguro` | Logs antigos, recriados |
| `~/Library/Developer/CoreSimulator/Caches` | `seguro` | Cache do simulador iOS |
| `~/Library/Developer/Xcode/DerivedData` | `seguro` | Artefatos de build recompiláveis |
| `~/Library/Developer/Xcode/DocumentationCache` | `seguro` | Documentação baixada |
| `~/.npm/_cacache`, `~/Library/pnpm/store`, `~/.cache` | `seguro` | Caches de ferramentas; pacotes são re-baixados |
| `~/.gradle/caches` | `seguro` | Dependências re-baixáveis |
| `/Library/Caches`, `/Library/Updates` | `seguro` | Cache do sistema e restos de atualização do macOS (precisa de `sudo`) |
| `~/Library/Application Support/<App>/**/Service Worker`, `IndexedDB` | `cuidado` | Parece cache, mas pode guardar dados offline de sites/apps |
| `~/Library/Application Support/<Editor>/User/workspaceStorage` | `cuidado` | Estado/layout dos workspaces (perde só histórico local) |
| `~/Library/Developer/CoreSimulator/Devices` | `cuidado` | Apps e dados dos simuladores iOS (não são cache) |
| `~/Library/Containers/com.docker.docker` | `cuidado` | Imagens e volumes do Docker |
| `~/Library/Application Support/MobileSync/Backup` | `cuidado` | Backup local de iPhone/iPad |
| `~/.Trash` | `cuidado` | Lixeira; confira o conteúdo antes |
| `~/Desktop`, `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Movies`, `~/Music` | `protegido` | Arquivos pessoais |
| `~/Library/Keychains`, `Mail`, `Messages` | `protegido` | Senhas, e-mails, mensagens |
| `/private/var/vm`, `/Applications` | `protegido` | Swap do sistema e apps instalados |

### Fluxo interno

1. **Catálogo** — monta a lista de caminhos existentes e classifica cada um.
2. **Medição** — calcula o tamanho real em disco (blocos de 512 bytes), em paralelo.
3. **`scan`** — agrupa por categoria e mostra o resumo.
4. **`clean`** — filtra apenas `seguro` (ou `seguro + cuidado` com `--category tudo`), pergunta o que remover e apaga item por item.

> Pastas como `~/Library/Caches` mudam de máquina para máquina; o catálogo é montado em tempo de execução, então a ferramenta funciona em outros Macs sem editar código.

## Como a segurança funciona

- **Dry-run primeiro**: `clean --dry-run` mostra exatamente o que seria apagado e quanto liberaria.
- **Confirmação explícita**: na limpeza interativa é preciso digitar `remover`.
- **Catálogo fechado**: só entram na limpeza caminhos descobertos pela própria ferramenta.
- **Guardas de caminho**: recusa `/`, a home inteira, `~/Library` e `~/Library/Application Support`.
- **Protegidos são intocáveis**: não há flag que os inclua.
- **Nada de `sudo` automático**: itens de `/Library` são pulados com aviso.

Três camadas de proteção, na prática:

1. A categoria `protegido` nunca entra na lista de remoção.
2. Mesmo que entrasse, `guard_path()` recusaria caminhos raiz ou sensíveis.
3. Mesmo que passasse, `is_writable()` impede apagar o que o usuário não tem permissão.

Para os itens de sistema:

```bash
sudo ./maccache.py clean --yes
```

## Testando com segurança

Antes de confiar na ferramenta, rode nesta ordem:

```bash
./maccache.py scan              # confira se os caminhos fazem sentido
./maccache.py clean --dry-run   # veja o que seria apagado, sem apagar
./maccache.py clean             # escolha 1 item pequeno e confirme
```

Depois de cada remoção, abra o app afetado para confirmar que ele recriou o cache normalmente.

## Exemplo de saída (desta máquina)

```
maccache — auditoria de cache do macOS
Disco: 228.3 GB total · 141.3 GB usados · 86.9 GB livres

SEGURO — pode apagar (16 itens, 9.8 GB)
     1.7 GB  ~/Library/Caches/BraveSoftware
   971.5 MB  ~/.npm/_cacache
             pacotes do npm
   748.2 MB  ~/Library/Application Support/Notion/Partitions/notion/Cache
             cache do Notion
...

CUIDADO — apague só se souber (9 itens, 9.3 GB)
     4.1 GB  ~/Library/Containers/com.docker.docker
             imagens e volumes (use docker system prune)
     1.2 GB  ~/Library/Application Support/BraveSoftware/Brave-Browser/Default/Service Worker
             pode conter dados offline do navegador
...

Resumo: seguro 9.8 GB · cuidado 9.3 GB · removível 19.1 GB
```

## Limitações

- **Espaço purgável do APFS** e snapshots locais do Time Machine não aparecem no tamanho das pastas. Veja com `tmutil listlocalsnapshots /`.
- Caches de apps **abertos** podem ser recriados logo depois da limpeza.
- Docker não é apagado pela ferramenta (é categoria `cuidado`); use `docker system prune -a` quando o Docker estiver rodando.
- `IndexedDB` e `Service Worker` ficam em `cuidado` porque podem guardar dados offline de sites (ex.: rascunhos, sessões).
- O tamanho é medido em blocos de disco, então pode divergir um pouco do "Sobre este Mac".
- **Não há desfazer**: a remoção é definitiva (não vai para a Lixeira). Por isso o `--dry-run` existe.

## Subindo para o Git

O projeto já tem `.gitignore` com `__pycache__/` e `*.pyc`. Para publicar:

```bash
cd ~/Documents/Projeto\ Padrão
git init
git add maccache.py README.md .gitignore
git commit -m "feat: CLI maccache para auditar e limpar cache do macOS"
git branch -M main
git remote add origin <URL-DO-SEU-REPO>
git push -u origin main
```

Se for criar o repositório pelo GitHub CLI:

```bash
gh repo create maccache --public --source=. --push
```

## Ideias para evoluir

- Agendar `scan` semanalmente com `launchd` e gerar um relatório.
- Adicionar `--older-than` (apagar só o que não é acessado há N dias).
- Exportar CSV/HTML do relatório.
- Integrar `brew cleanup` e `docker system prune` como ações opcionais.
