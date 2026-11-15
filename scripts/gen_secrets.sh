#!/bin/sh
# Gera segredos fortes para o .env (não reutilize entre ambientes).
set -eu
rnd() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }
echo "RSSP_SECRET_KEY=$(rnd 48)"
echo "RSSP_ENCRYPTION_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
echo "POSTGRES_PASSWORD=$(rnd 24)"
echo "ICECAST_SOURCE_PASSWORD=$(rnd 18)"
echo "ICECAST_ADMIN_PASSWORD=$(rnd 18)"
