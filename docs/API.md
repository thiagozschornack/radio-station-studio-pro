# API — Fase 1

Base: `/api`. JSON UTF-8. Autenticação: `Authorization: Bearer <access_token>`.
Erros: `{"error": "mensagem", "code": "opcional"}` com status 400/401/403/404/409/413/415/422/423/429/500.
Para rotas de emissora, **404** também significa “você não é membro desta emissora”.

## Autenticação e conta

| Método | Rota | Descrição |
|---|---|---|
| POST | `/auth/login` | `{email, password}` → `{access_token, user}` + cookie de refresh, ou `{mfa_required, challenge}` |
| POST | `/auth/mfa` | `{challenge, code}` → sessão |
| POST | `/auth/refresh` | usa o cookie `rssp_refresh`; rotaciona o token |
| POST | `/auth/logout` | revoga o refresh atual |
| GET | `/me` | usuário + emissoras com papel e permissões |
| POST | `/me/password` | `{current_password, new_password}`; encerra as outras sessões |
| POST | `/me/mfa/setup` · `/me/mfa/enable` · `/me/mfa/disable` | TOTP (RFC 6238) |

## Plataforma (superadministrador)

| Método | Rota | Descrição |
|---|---|---|
| GET/POST | `/admin/users` | lista / cria usuário (senha temporária exibida uma vez) |
| POST | `/stations` | `{slug, name, owner_email?}` cria emissora, stream e estado de playout |

## Emissora — `/stations/{sid}`

| Método | Rota | Permissão | Descrição |
|---|---|---|---|
| GET / PATCH | `` | station.view / station.edit | dados, fuso, cor, histórico público |
| GET / POST | `/members` | station.view / members.manage | equipe; POST cria usuário se o e-mail não existir |
| DELETE | `/members/{uid}` | members.manage | preserva ao menos um proprietário |
| GET / PUT | `/stream` | stream.view / stream.edit | configuração; `source_password` é somente escrita |
| POST | `/stream/start` · `/stream/stop` | stream.control | liga/desliga a transmissão |
| GET | `/status` | station.view | dashboard: no ar, próximo, fila, ouvintes, saúde, alertas |
| POST | `/playout/skip` | playout.skip | pula a faixa atual |
| GET / POST / DELETE | `/queue` | queue.view / queue.edit | fila; POST `{media_id \| media_ids[], where: "end"\|"next"}`; DELETE limpa |
| DELETE | `/queue/{qid}` | queue.edit | remove item pendente |
| POST | `/queue/{qid}/move` | queue.edit | `{index}` reordena |
| GET | `/media` | library.view | `q, kind, license_status, sort, limit, offset, archived` |
| POST | `/media` | library.upload | multipart: `file`, `kind?`, `title?`, `artist?` |
| GET / PATCH / DELETE | `/media/{mid}` | library.view / library.edit | detalhes + execuções; edição; exclusão (ou arquivamento, se já passou pela fila) |
| POST | `/media/{mid}/reanalyze` | library.edit | reenfileira a análise |
| GET | `/media/{mid}/preview-url` | library.view | URL assinada de 10 min para pré-escuta |
| GET | `/media/{mid}/cover` | library.view | capa JPEG |
| GET | `/plays` · `/plays.csv` | reports.view | execuções reais (`from`, `to` ISO-8601 UTC) |
| GET | `/listeners?hours=24` | reports.view | série de conexões simultâneas |
| GET | `/audit` | audit.view | últimos 300 eventos |

Campos editáveis da mídia: `title, artist, album, genre, year, bpm, rating, tags[], kind, cue_in_ms, cue_out_ms, fade_in_ms, fade_out_ms, isrc`.
Campos de licença (`library.license`): `license_status` (`unknown, licensed, own, public_domain, creative_commons, blocked`), `license_expires_at` (AAAA-MM-DD), `rights_holder`, `license_ref`.

## Público (sem autenticação)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/public/stations/{slug}` | nome, identidade, URL pública do stream, no ar, faixa atual, histórico (se permitido) |
| GET | `/public/stations/{slug}/status` | para monitores de disponibilidade |
| GET | `/public/stations/{slug}/cover/{mid}` | capa |
| GET | `/health` · `/meta` | saúde da API · estado real dos módulos |
