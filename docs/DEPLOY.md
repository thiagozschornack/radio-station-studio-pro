# Implantação

## Onde hospedar

| Ambiente | Serve? | Motivo |
|---|---|---|
| Hospedagem compartilhada (cPanel) | **Não** | sem processos permanentes (playout, worker), sem Icecast, sem portas próprias |
| VPS Linux (2 vCPU, 2–4 GB RAM, 40 GB+ SSD) | **Sim** | recomendado para começar |
| VPS + CDN de áudio / Icecast em relay | Sim | para audiências grandes (Fase 4) |

**Tráfego:** cada ouvinte a 128 kbps consome ~58 MB/h. São 100 ouvintes simultâneos em média por 24 h ≈ 4,2 TB/mês. Confira a franquia de transferência da VPS antes de definir limites comerciais.

## Implantação com Docker (recomendada)

1. **DNS:** crie dois registros A apontando para a VPS: `radio.seudominio.com.br` (painel/player) e `stream.seudominio.com.br` (áudio).
2. **Servidor:** instale Docker Engine + Compose v2 e libere as portas 80/443 no firewall.
3. **Configuração:**
   ```bash
   git clone <repositório> rssp && cd rssp
   cp .env.example .env
   ./scripts/gen_secrets.sh      # copie as linhas geradas para o .env
   nano .env                     # domínios, e-mail do ACME
   chmod 600 .env
   ```
4. **Subir:** `docker compose up -d --build`
5. **Primeiro acesso:**
   ```bash
   docker compose exec api python -m rssp.cli create-superadmin --email voce@seudominio.com.br --name "Seu Nome"
   ```
   Entre em `https://radio.seudominio.com.br/admin/`, crie a emissora (*Plataforma → Emissoras*) e configure *Transmissão*:
   - Host `icecast`, porta `8000`, mountpoint `/<slug>`, usuário `source`, senha = `ICECAST_SOURCE_PASSWORD` do `.env`
   - URL pública: `https://stream.seudominio.com.br/<slug>`
   - URL de status: `http://icecast:8000/status-json.xsl`
6. Envie áudios, monte a fila e clique em **Iniciar transmissão**. Teste de outro dispositivo em `https://radio.seudominio.com.br/r/<slug>`.

Demo opcional: `docker compose exec api python -m rssp.cli seed-demo --owner voce@seudominio.com.br`.

### Atualização
```bash
git pull && docker compose up -d --build     # migrações rodam automaticamente na inicialização
```
O playout é reiniciado com `stop_grace_period` de 20 s: a faixa em andamento é registrada como interrompida e o item manual volta à fila.

### Backups
`./scripts/backup.sh /caminho/backups` gera `rssp.dump` (pg_dump) e `storage.tgz`. Agende no cron e **copie para fora da VPS**. A restauração está nos comentários do script. Teste a restauração periodicamente (checklist em `HOMOLOGACAO.md`).

## Implantação sem Docker (systemd)

1. `apt install python3-venv ffmpeg postgresql icecast2 nginx certbot python3-certbot-nginx`
2. Código em `/opt/rssp`; `python3 -m venv /opt/rssp/venv && /opt/rssp/venv/bin/pip install -r backend/requirements.txt`
3. Usuário `rssp`; diretório `/var/lib/rssp` (dono `rssp`); arquivo `/etc/rssp/rssp.env` (modo 600) com as variáveis do `.env.example`, `RSSP_ENV=production`, `RSSP_DATABASE_URL=postgresql://...`, `RSSP_STORAGE_PATH=/var/lib/rssp/storage`, `RSSP_COOKIE_SECURE=1`, `RSSP_TRUST_PROXY=1`
4. Icecast: use `deploy/icecast/icecast.xml.template` como base para `/etc/icecast2/icecast.xml`
5. `cp deploy/systemd/*.service /etc/systemd/system && systemctl enable --now rssp-api rssp-worker rssp-playout`
6. nginx: `deploy/nginx.conf.example` + `certbot --nginx`

## HTTPS e “conteúdo misto”
Páginas HTTPS não podem tocar áudio de URL `http://`. Por isso o stream público passa pelo proxy HTTPS (`stream.seudominio.com.br`). Em `docker-compose.yml`, o Caddy emite os certificados automaticamente e bloqueia as páginas administrativas do Icecast no domínio público.

## Variáveis de ambiente
Todas estão em `.env.example`. Em produção (`RSSP_ENV != development`), a aplicação **se recusa a iniciar** sem `RSSP_SECRET_KEY` (≥ 32 caracteres) e `RSSP_ENCRYPTION_KEY`.
Trocar `RSSP_ENCRYPTION_KEY` invalida as senhas de source e os segredos de MFA já salvos. Nesse caso, salve a senha de source novamente na tela Transmissão.

## Monitoramento
- `GET /api/health`: API + banco (usado pelo health check do contêiner)
- `GET /api/public/stations/<slug>/status`: `on_air` e ouvintes, para UptimeRobot ou similar
- `docker compose logs -f playout`: eventos de ar, reconexões e erros por emissora
