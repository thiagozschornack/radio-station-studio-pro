#!/bin/sh
set -eu
: "${ICECAST_SOURCE_PASSWORD:?defina ICECAST_SOURCE_PASSWORD}"
: "${ICECAST_ADMIN_PASSWORD:?defina ICECAST_ADMIN_PASSWORD}"
: "${ICECAST_RELAY_PASSWORD:=$(head -c 24 /dev/urandom | base64)}"
: "${ICECAST_HOSTNAME:=localhost}"
: "${ICECAST_MAX_CLIENTS:=500}"
: "${ICECAST_MAX_SOURCES:=20}"
export ICECAST_SOURCE_PASSWORD ICECAST_ADMIN_PASSWORD ICECAST_RELAY_PASSWORD ICECAST_HOSTNAME ICECAST_MAX_CLIENTS ICECAST_MAX_SOURCES
envsubst < /etc/icecast2/icecast.xml.template > /etc/icecast2/icecast.xml
chown icecast2 /etc/icecast2/icecast.xml && chmod 600 /etc/icecast2/icecast.xml
exec su -s /bin/sh icecast2 -c "icecast2 -c /etc/icecast2/icecast.xml"
