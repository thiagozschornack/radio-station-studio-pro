# RADIO STATION STUDIO PRO — EmpreenderTHZ

**Sua rádio. Sua programação. Seu universo sonoro.**

Plataforma para criar, operar, automatizar e transmitir rádios digitais pela internet, com várias emissoras isoladas por conta.
Esta é a **entrega da Fase 1**: arquitetura, autenticação, banco de dados, biblioteca, player e **transmissão real funcional**.

> Nada aqui é simulado. Recursos de fases futuras aparecem no painel como **indisponíveis**, com a fase prevista.
> O mapa oficial de estados está em `backend/rssp/status.py` e é exibido em *Plataforma → Estado dos módulos*.

## O que funciona nesta versão

| Área | Estado | Destaques |
|---|---|---|
| Transmissão digital | ✅ funcional (requer Icecast) | Encoder FFmpeg → Icecast 2.4 (MP3, AAC, Opus/Ogg), bitrate/sample rate/canais, metadados, reconexão com backoff, endpoint de status, player HTML5 |
| Playout 24/7 | ✅ funcional | Serviço independente do navegador; fila persistente; troca de faixa sem lacuna; rotação de contingência sem repetição; mídia de contingência; retomada após `kill -9`; instância única por emissora |
| Registro de execuções | ✅ funcional | Cada execução real com início, fim, tempo efetivamente transmitido e motivo (concluída, pulada, interrompida, parada, erro). Não conta como “tocado” o que foi ao ar com o servidor de streaming fora |
| Biblioteca | ✅ funcional | Upload validado por FFprobe, deduplicação SHA-256, metadados e capa, loudness EBU R128 + true peak, detecção de silêncio, cue in/out e fades não destrutivos, tags, BPM, licença, pré-escuta |
| Segurança | ✅ funcional | scrypt, JWT curto + refresh rotativo (cookie httpOnly) com detecção de reuso, MFA TOTP, bloqueio por tentativas, rate limit, RBAC com 8 papéis, isolamento entre emissoras, credenciais cifradas, auditoria |
| Dashboard | ◐ parcial | No ar, próximo, restante, fila, ouvintes reais do Icecast, saúde do playout/encoder, alertas, iniciar/parar, pular. Grade do dia e receitas: fases 2 e 3 |
| Estúdio ao vivo, grade, playlists, podcasts, publicidade, IA, PWA, planos | ⏳ planejado | Fases 2–4 (veja `docs/ROADMAP.md`) |

## Início rápido (desenvolvimento local)

Requisitos: Python 3.11+, FFmpeg/FFprobe no PATH.

```bash
pip install -r backend/requirements-dev.txt
./scripts/dev_up.sh
```

Abra `http://127.0.0.1:8080/admin/` (login `admin@local.dev`, senha `TroqueEssaSenha1`, ou defina `ADMIN_PW`) e o player em `http://127.0.0.1:8080/r/demo-thz`.
O script usa SQLite e o **mini-icecast**, um servidor de *teste* compatível com o protocolo Icecast (`tools/mini_icecast.py`). Em produção use Icecast de verdade.
A emissora demo usa somente **áudios sintéticos gerados pelo FFmpeg**, identificados como “DEMO”, sem música de terceiros.

## Produção

Veja **`docs/DEPLOY.md`**. Resumo: VPS Linux com Docker → `cp .env.example .env` → `./scripts/gen_secrets.sh` → `docker compose up -d --build`.
A pilha sobe PostgreSQL 16, Icecast 2.4, API, worker, playout e Caddy (HTTPS automático para painel e stream).

> **Importante:** hospedagem compartilhada (cPanel) **não** roda o playout nem o Icecast, porque ambos precisam de processos permanentes e portas próprias. É necessária uma VPS (ou servidor dedicado).

## Testes

```bash
cd backend && python -m pytest -q          # 26 testes, ~60 s
```

Incluem testes de **transmissão ponta a ponta**: o playout envia áudio real por protocolo Icecast, um ouvinte externo recebe o fluxo e o FFprobe o decodifica (MP3, AAC e Opus), além de pulo, queda e retorno do servidor, `kill -9` com recuperação, instância única, rotação e bloqueio por licença.
Para rodar contra PostgreSQL: `RSSP_TEST_DATABASE_URL=postgresql://... python -m pytest`.

## Estrutura

```
backend/rssp/
  api/           HTTP (Starlette): auth, emissoras, biblioteca, operação, público
  playout/       serviço de playout (engine) e cliente Icecast
  migrations/    SQL portável (PostgreSQL em produção, SQLite em dev/testes)
  jobs.py        fila de trabalhos persistente + worker (análise de mídia)
  media.py       FFprobe/FFmpeg: validação, loudness, silêncio, capa
  security.py    senhas, tokens, MFA, cifragem, URLs assinadas, rate limit
  rbac.py        papéis e permissões    status.py  estado real dos módulos
  cli.py         migrate, create-superadmin, seed-demo
backend/tests/   unitários, integração, isolamento, RBAC e E2E de transmissão
web/admin/       painel administrativo (SPA sem build)
web/player/      player público por emissora (/r/<slug>)
deploy/          Icecast, Caddy, nginx, systemd
docs/            arquitetura, API, implantação, manuais, homologação, conformidade
tools/           mini-icecast (somente teste)
```

## Documentação

- `docs/ARQUITETURA.md` — componentes, fluxo do áudio, decisões e limites
- `docs/API.md` — endpoints
- `docs/DEPLOY.md` — implantação, HTTPS, backups, atualização
- `docs/MANUAL-OPERADOR.md` e `docs/MANUAL-ADMIN.md`
- `docs/HOMOLOGACAO.md` — checklist e critérios de aceite, com o que foi verificado
- `docs/CONFORMIDADE.md` — direitos autorais, ECAD, LGPD
- `docs/ROADMAP.md` — fases 2 a 4
