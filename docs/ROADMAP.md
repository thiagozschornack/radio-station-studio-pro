# Roadmap

## Fase 1 — entregue
Arquitetura, autenticação (MFA, RBAC), PostgreSQL, biblioteca com análise, fila persistente, playout independente, transmissão Icecast (MP3/AAC/Opus), player público, relatórios de execução e conexões, auditoria, Docker/HTTPS.

## Fase 2 — estúdio, automação, agendamento e fallback
- Migrar o painel para React/Next.js + TypeScript (UI do estúdio exige componentes ricos)
- **Estúdio ao vivo**: canais (mic, players, cartucheira), faders, mute/solo, PFL, medidores pico/RMS/LUFS em AudioWorklet, EQ, compressor, limiter, ducking, hotkeys, seleção de dispositivos
- **Entrada ao vivo no playout**: ingestão WebRTC/SRT ou source dedicado; alternância ao vivo ↔ automação com prioridade e retorno automático
- **Relógios de programação**, blocos horários, categorias, separação de artista, calendário (dia/semana/mês/feriados), conflitos, contingência por horário
- Crossfade sobreposto, inserção de vinheta e **emergência** com prioridade, gravação contínua (arquivo legal)
- Redis para eventos em tempo real (SSE/WebSocket no painel)

## Fase 3 — podcasts, publicidade, portal e analytics
- Podcast Studio (séries, episódios, capítulos, RSS) e agendamento no ar
- Comercial: anunciantes, campanhas, contratos, mapa comercial, **comprovantes a partir do `play_log`**
- Portal público completo + PWA (mini-player persistente, favoritos)
- Analytics a partir de logs do Icecast/CDN: sessões, duração, dispositivos, geografia aproximada, anonimização e retenção (LGPD)

## Fase 4 — IA, multiemissora avançada, SaaS e escala
- IA: assistente de programação, roteiros, pautas com fontes, transcrição, classificação; TTS com vozes licenciadas
- Planos Free/Creator/Pro/Business/Enterprise, limites, gateway de pagamento, faturamento
- Domínios por emissora, relays/CDN, rate limit distribuído, observabilidade (Prometheus/Grafana), CI/CD
- Apps nativos Android/iOS (a partir da arquitetura do PWA)
