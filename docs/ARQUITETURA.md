# Arquitetura — Fase 1

## Visão geral

```
                    ┌──────────────── VPS ────────────────────────────────────────────┐
 Navegador (painel) │  Caddy (HTTPS) ──► API Starlette ──► PostgreSQL                 │
 ──────────────────►│        │              │   ▲              ▲   ▲                    │
                    │        │              │   │ jobs          │   │ fila, comandos,   │
                    │        │              ▼   │               │   │ log, heartbeat    │
                    │        │         Armazenamento ◄── Worker │   │                   │
                    │        │         (disco / S3)    (FFmpeg) │   │                   │
                    │        │              │                   │   │                   │
                    │        │              └──────────► Playout (1 processo por emissora)
                    │        │                           decodifica → PCM → encoder FFmpeg
                    │        │                                       │ HTTP PUT (source)
 Ouvintes (player)  │        ▼                                       ▼                   │
 ──────────────────►│  Caddy (HTTPS) ───────────────────────────► Icecast 2.4            │
                    └──────────────────────────────────────────────────────────────────┘
```

Os três planos pedidos ficam separados:

| Plano | Componente | Observação |
|---|---|---|
| Administração | `web/admin` + `rssp.api` | painel, biblioteca, equipe, relatórios |
| Operação / estúdio | dashboard + `rssp.playout` | o playout roda **no servidor**; fechar o navegador não interrompe nada |
| Transmissão | Icecast + proxy HTTPS | ingestão (playout → Icecast) separada da distribuição (Icecast → ouvintes) |
| Público | `web/player` + `/api/public/*` | sem autenticação; nunca recebe credenciais nem host interno |

## Stack e justificativas

| Camada | Escolha | Motivo |
|---|---|---|
| API | Python 3.11+ / Starlette (ASGI) | o prompt admite FastAPI ou equivalente; Starlette é a base do FastAPI, com menos dependências |
| Banco | PostgreSQL 16 | requisito; migração SQL portável também roda em SQLite para dev/testes |
| Fila de trabalhos | tabela `jobs` no PostgreSQL (`FOR UPDATE SKIP LOCKED`) | durabilidade sem componente extra na Fase 1; Redis entra como cache e pub/sub na Fase 2/4 |
| Áudio | FFmpeg/FFprobe | ingestão, análise EBU R128, decodificação e codificação |
| Streaming | Icecast 2.4 | protocolo aberto, MP3/AAC/Ogg, status JSON |
| Frontend | HTML/CSS/JS modular, sem build | entrega imediatamente implantável. Migrar para React/Next.js + TypeScript é recomendado quando entrar o Estúdio (Fase 2), que tem UI mais complexa |
| Infra | Docker Compose + Caddy | HTTPS automático, health checks, reinício automático |

## Motor de playout

Um processo por emissora (`python -m rssp.playout --all` supervisiona e reinicia os filhos). Dentro dele:

- **Controller** (thread principal): escolhe o próximo item, registra execuções, lê comandos (`playout_commands`), envia metadados, coleta ouvintes e grava heartbeat a cada 2 s. É a única parte que acessa o banco.
- **Pump** (thread de tempo real): lê PCM 48 kHz/16 bits/estéreo dos decodificadores FFmpeg e o entrega ao encoder no ritmo do relógio, com ~400 ms de folga. Ela pré-carrega a próxima faixa, então a troca acontece **sem lacuna**. Faltando conteúdo, entrega silêncio para não derrubar ouvintes, e o status passa a `dead_air`, com alerta.
- **Encoder + Sender**: um processo FFmpeg codifica em MP3/AAC/Opus. Uma thread envia o resultado por **HTTP PUT** ao Icecast; o próprio serviço faz o envio para que a senha de source **não apareça na linha de comando** (`ps`). Se a conexão cai, há reconexão com backoff exponencial (1 s → 30 s), e a conexão nova sempre recebe um encoder novo, para que fluxos Ogg/Opus comecem com cabeçalhos válidos.

**Prioridade editorial:** um item da fila manual sempre substitui uma escolha da rotação ainda não iniciada.

**Comprovação fiel:** enquanto o servidor de streaming está fora, a faixa fica *segura* e não avança. `played_ms` registra só o áudio realmente entregue.

**Recuperação:** ao iniciar, execuções abertas são fechadas como `interrupted` (fim = último heartbeat), e o item manual em andamento volta ao topo da fila. Um *lease* por heartbeat impede duas instâncias na mesma emissora; se o dono anterior morreu na mesma máquina, a retomada é imediata.

**Normalização:** o ganho por faixa vem da análise (alvo padrão −16 LUFS), limitado para manter o true peak ≤ −1 dBTP, com limitador de segurança.

## Multiemissora e segurança

- Toda tabela de dados de emissora tem `station_id`. Toda rota de emissora passa por `Ctx.station(perm)`, que devolve **404** a quem não é membro (não revela a existência de outras emissoras) e **403** a quem é membro sem a permissão.
- Papéis: superadministrador (global), proprietário, diretor, programador, locutor, comercial, analista e operador (`rbac.py`).
- Senha de source cifrada com Fernet (`RSSP_ENCRYPTION_KEY`). A API retorna apenas `has_source_password`.
- Tokens: acesso JWT de 15 min em memória no navegador; refresh de 14 dias em cookie `httpOnly; SameSite=Strict; Path=/api/auth`, rotacionado a cada uso. O reuso de um refresh antigo revoga todas as sessões do usuário.
- Uploads: limite de tamanho, lista de extensões, validação real por FFprobe (contêiner, codec, duração), nome gerado pelo sistema e proteção contra *path traversal* no armazenamento.
- Cabeçalhos: CSP restritiva (sem script inline), `X-Frame-Options: DENY`, `nosniff`, HSTS em produção.
- Auditoria de login, alterações de emissora/stream/membros/mídia/fila e exportações.

## Limites conhecidos (Fase 1)

- O rate limit é por processo (em memória). Com várias instâncias da API, migrar para Redis (Fase 4).
- Metadados de título no Icecast via `/admin/metadata` valem para MP3/AAC. Em Opus/Ogg o player público obtém o título pela API.
- A transição entre faixas é com fades, sem crossfade sobreposto (Fase 2).
- Ouvintes = conexões simultâneas do Icecast. Sessões, usuários únicos e geografia exigem processar logs de acesso (Fase 3).
- O backend S3 está implementado, mas depende de credenciais e não foi testado neste pacote.
