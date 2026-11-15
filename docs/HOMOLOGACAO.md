# Homologação — Fase 1

Legenda: **✅ AUTO** = coberto por teste automatizado e executado com sucesso no ambiente de desenvolvimento ·
**🔶 VPS** = precisa ser executado no ambiente real (Icecast de verdade, rede externa, Docker).

## Critérios de aceite obrigatórios

| # | Critério | Verificação | Evidência |
|---|---|---|---|
| 1 | Transmitir áudio real pela internet | ✅ AUTO (protocolo Icecast) · 🔶 VPS (Icecast real + internet) | `test_e2e_mp3_full_cycle`, `test_e2e_other_formats`: ouvinte recebe MP3/AAC/Opus e o FFprobe decodifica |
| 2 | Programação sem navegador aberto | ✅ AUTO | o playout é processo próprio; os testes o operam sem nenhum navegador |
| 3 | Alternar ao vivo ↔ automação | ⏳ Fase 2 | não há entrada ao vivo nesta fase; ligar/desligar a transmissão e a fila ↔ rotação estão cobertos |
| 4 | Registrar corretamente as faixas | ✅ AUTO | início/fim/tempo transmitido/motivo; não conta o tempo com o servidor fora |
| 5 | Suportar reinício do serviço | ✅ AUTO | `kill -9` → execução marcada como interrompida → item manual retomado e concluído |
| 6 | Escuta em outro dispositivo | 🔶 VPS | abrir `/r/<slug>` em celular fora da rede da VPS |
| 7 | Isolamento entre emissoras | ✅ AUTO | `test_cross_tenant_access_denied` (10 rotas, IDs cruzados, URL assinada) |
| 8 | Proteger credenciais | ✅ AUTO | senha cifrada no banco; ausente em respostas, página pública, auditoria e linha de comando |
| 9 | Relatórios de dados reais | ✅ AUTO | sem dado, mostra “sem dado”/“indisponível”; nenhum número inventado |
| 10 | Implantação reproduzível | ✅ AUTO (dev) · 🔶 VPS (Docker) | `scripts/dev_up.sh` validado do zero; `docker-compose.yml` validado sintaticamente, **não executado** (sem Docker neste ambiente) |

## Suítes automatizadas (26 testes)

| Arquivo | Cobre |
|---|---|
| `test_auth_security.py` | scrypt, Fernet, TOTP (vetor RFC 6238), rate limit, login, rotação/reuso de refresh, bloqueio após 5 falhas, MFA, política de senha, cabeçalhos, URL assinada expirada, rotas de superadmin |
| `test_tenancy_rbac.py` | isolamento entre emissoras; matriz de papéis; último proprietário; convite com senha temporária |
| `test_library_stream.py` | upload com metadados, capa, loudness, silêncio e cue sugerido; Range na pré-escuta; arquivos falsos/extensão proibida/duplicados; WAV/FLAC/Opus; edição e validação de cues; busca Unicode; licença bloqueada/vencida; fila (fim, próxima, mover, remover); arquivamento; configuração do stream sem vazamento; status sem números inventados |
| `test_playout_e2e.py` | MP3 ponta a ponta, metadados, status coerente, pulo, instância única, `kill -9` e retomada, queda e volta do servidor com reconexão, AAC e Opus decodificáveis, rotação sem repetição consecutiva, mídia bloqueada nunca vai ao ar, parar transmissão libera o mountpoint |

Adicionalmente: a migração foi aplicada em **PostgreSQL 16 real** (13 tabelas), incluindo a consulta da fila com `FOR UPDATE SKIP LOCKED`. A aplicação completa sobre PostgreSQL requer `psycopg` (em `requirements.txt`) e deve ser validada na VPS com `RSSP_TEST_DATABASE_URL=postgresql://… pytest`.
Interface verificada em navegador (Chromium) em desktop, celular, tema claro e escuro; o player público tocou o stream ao vivo.

## Checklist na VPS (executar e registrar data/responsável)

- [ ] `docker compose up -d --build` sem erros; `docker compose ps` com todos os serviços *healthy/running*
- [ ] HTTPS válido em `APP_DOMAIN` e `STREAM_DOMAIN`
- [ ] `/admin` e `/status.xsl` do Icecast **não** acessíveis em `STREAM_DOMAIN`
- [ ] Superadmin criado; MFA ativado
- [ ] Emissora criada; stream configurado com host `icecast`; **Iniciar** → ON AIR em até 5 s
- [ ] Escuta em celular via 4G (fora da rede) por 10 min sem cortes
- [ ] Título atualizado no player ao trocar a faixa
- [ ] `docker compose restart playout` → retoma em segundos; relatório mostra “interrompida”
- [ ] `docker compose restart icecast` → reconecta sozinho; contador de reconexões incrementa
- [ ] `docker compose restart api` → transmissão **não** é afetada
- [ ] Upload de arquivo corrompido → recusado com mensagem clara
- [ ] Segundo usuário em outra emissora não enxerga a primeira
- [ ] `RSSP_TEST_DATABASE_URL=postgresql://… pytest` verde contra o PostgreSQL da VPS
- [ ] `scripts/backup.sh` → restaurar em VPS limpa → biblioteca e relatórios íntegros
- [ ] Teste de carga de ouvintes (ex.: 200 conexões simultâneas com `ffmpeg -i URL -f null -` em paralelo) observando CPU, banda e estabilidade
- [ ] Monitor externo configurado em `/api/public/stations/<slug>/status`
