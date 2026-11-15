"""Estado real de cada módulo do produto. Exibido no painel para que nenhum recurso
não implementado seja apresentado como operacional.

status: implemented | partial | credentials | planned
"""

MODULES = [
    {"id": "dashboard", "name": "Dashboard central", "phase": 1, "status": "partial",
     "done": "Status da emissora, no ar, próximo, restante, fila, ouvintes (Icecast), saúde do playout/encoder, "
             "carga do host de playout, armazenamento, alertas, iniciar/parar transmissão, pular faixa.",
     "missing": "Grade do dia (Fase 2), receitas (Fase 3), inserir vinheta/emergência com prioridade (Fase 2)."},
    {"id": "studio", "name": "Estúdio ao vivo", "phase": 2, "status": "planned",
     "missing": "Mesa com canais, faders, PFL, medidores LUFS, processamento AudioWorklet e entrada ao vivo."},
    {"id": "streaming", "name": "Transmissão digital", "phase": 1, "status": "credentials",
     "done": "Encoder FFmpeg → Icecast (MP3/AAC/Opus), bitrate/sample rate/canais, mountpoint, metadados, "
             "reconexão com backoff, status público, player HTML5. Credenciais cifradas no servidor.",
     "missing": "Requer um servidor Icecast próprio e suas credenciais. CDN e gravação contínua: Fase 4/2."},
    {"id": "automation", "name": "Automação 24/7", "phase": 2, "status": "partial",
     "done": "Serviço de playout independente do navegador, fila persistente, rotação de contingência com "
             "janela de não repetição, mídia de contingência, recuperação após reinício.",
     "missing": "Relógios de programação, blocos horários, regras de separação, agendamento por calendário."},
    {"id": "library", "name": "Biblioteca musical e sonora", "phase": 1, "status": "implemented",
     "done": "Upload validado (FFprobe), deduplicação SHA-256, metadados, capa, loudness EBU R128, true peak, "
             "detecção de silêncio, cue in/out e fades não destrutivos, tags, BPM, classificação, licença, "
             "pré-escuta por URL assinada, histórico de execução.",
     "missing": "Edição por forma de onda e normalização offline de arquivo (Fase 2)."},
    {"id": "playlists", "name": "Playlists inteligentes", "phase": 2, "status": "planned"},
    {"id": "ai", "name": "Inteligência artificial", "phase": 4, "status": "planned",
     "missing": "Dependerá de provedores de IA/TTS com credenciais e vozes licenciadas."},
    {"id": "podcast", "name": "Podcast Studio", "phase": 3, "status": "planned"},
    {"id": "ads", "name": "Vinhetas e publicidade", "phase": 3, "status": "planned",
     "missing": "Campanhas, contratos e comprovantes. O log de execução real já é a base da comprovação."},
    {"id": "portal", "name": "Portal público", "phase": 3, "status": "partial",
     "done": "Player público por emissora com no ar, capa, histórico, reconexão e Media Session.",
     "missing": "Site completo com programação, locutores, notícias, PWA."},
    {"id": "mobile", "name": "App mobile / PWA", "phase": 4, "status": "planned"},
    {"id": "analytics", "name": "Analytics e audiência", "phase": 3, "status": "partial",
     "done": "Série de conexões simultâneas a partir do Icecast e relatório de execuções (CSV).",
     "missing": "Sessões, duração, origem, dispositivos e anonimização a partir de logs de acesso."},
    {"id": "multistation", "name": "Gestão multiemissora", "phase": 4, "status": "partial",
     "done": "Várias emissoras isoladas, RBAC com 8 papéis, membros por emissora.",
     "missing": "Domínios próprios por emissora e relatórios consolidados."},
    {"id": "saas", "name": "Monetização e SaaS", "phase": 4, "status": "planned",
     "missing": "Planos, limites e gateway de pagamento (dependente de credenciais)."},
    {"id": "security", "name": "Configurações e segurança", "phase": 1, "status": "implemented",
     "done": "Senhas scrypt, JWT curto + refresh rotativo em cookie httpOnly, detecção de reuso, MFA TOTP, "
             "bloqueio por tentativas, rate limiting, RBAC, isolamento por emissora, segredos em variáveis de "
             "ambiente, credenciais cifradas (Fernet), cabeçalhos de segurança, auditoria."},
    {"id": "rights", "name": "Direitos autorais e conformidade", "phase": 1, "status": "partial",
     "done": "Cadastro de titularidade/licença e vigência, bloqueio administrativo, relatório de execuções com ISRC.",
     "missing": "Relatórios no formato exigido pelo ECAD e políticas de uso. O sistema não substitui licenças."},
]
