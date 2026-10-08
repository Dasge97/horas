#!/bin/sh
# Versión para el servidor (codehive). La ejecuta cron cada noche.
# Trae lo último del repositorio (el PC sube sesiones.json cuando está encendido),
# calcula y publica horas.json. Los errores quedan en registro.log y, si la bóveda
# está desbloqueada, se avisa al móvil por ntfy.

set -u
AQUI=$(cd "$(dirname "$0")" && pwd)
REGISTRO="$AQUI/registro.log"
cd "$AQUI" || exit 1

anotar() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$REGISTRO"; }

avisar() {
  # Mejor esfuerzo: si la bóveda está bloqueada, no hay aviso y el error queda solo en el registro.
  /home/codehive/bin/vault-usar "ntfy - token scripts" NTFY_TOKEN -- sh -c \
    "curl -s -H \"Authorization: Bearer \$NTFY_TOKEN\" -H \"Title: horas: fallo al publicar\" -H \"Tags: hourglass\" -d \"$1\" https://ntfy.code-hive.space/claude" \
    >/dev/null 2>&1 || true
}

fallo() { anotar "ERROR: $1"; avisar "$1"; exit 1; }

anotar "Inicio"
git pull -q --rebase origin main || fallo "git pull falló"
salida=$(python3 -I calcular.py 2>&1) || fallo "calcular.py falló: $(printf '%s' "$salida" | tail -3)"
anotar "$(printf '%s' "$salida" | grep '^Total:' | tail -1)"

git add horas.json
if git diff --cached --quiet; then anotar "Sin cambios, no se publica"; exit 0; fi
git commit -q -m "Horas al $(date '+%Y-%m-%d')" || fallo "git commit falló"
git push -q origin main || fallo "git push falló"
anotar "Publicado"
