#!/bin/sh
# Versión para el servidor (codehive). La ejecuta cron cada noche.
# Trae lo último del repositorio (el PC sube sesiones.json cuando está encendido),
# calcula y publica horas.json. Todo queda en registro.log. El resultado se informa
# al Kit con kit-estado: el panel lo enseña y, si falla, el Kit avisa al móvil.
#
# El servidor no tiene nada propio que guardar: solo publica ficheros generados. Por eso cada intento
# parte de lo último de GitHub (git reset --hard). Si el PC publica a la vez y el push se rechaza, se
# vuelve a empezar desde lo último, hasta 3 veces. Así nunca se queda atascado en un conflicto.
#
# Todo va dentro de main(): sh lee la función entera antes de ejecutarla, así que el git reset puede
# cambiar este mismo fichero sin afectar a la ejecución en curso.

main() {
  set -u
  AQUI=$(cd "$(dirname "$0")" && pwd)
  REGISTRO="$AQUI/registro.log"
  KIT_ESTADO="$HOME/bin/kit-estado"
  cd "$AQUI" || exit 1

  # Escribe en registro.log. Solo lo repite en pantalla si se lanza a mano: cron ya manda la salida
  # al mismo registro.log y cada línea saldría dos veces.
  anotar() {
    linea="$(date '+%Y-%m-%d %H:%M:%S') $1"
    printf '%s\n' "$linea" >> "$REGISTRO"
    [ -t 1 ] && printf '%s\n' "$linea"
    return 0
  }

  informar() { # informar <ok|error> <mensaje>
    [ -x "$KIT_ESTADO" ] && "$KIT_ESTADO" horas-codehive "$1" "$2" --titulo "Horas en codehive" >/dev/null 2>&1
    return 0
  }

  fallo() { anotar "ERROR: $1"; informar error "$1"; exit 1; }

  anotar "Inicio"
  intento=1
  while :; do
    git fetch -q origin || fallo "git fetch falló"
    git rebase --abort >/dev/null 2>&1
    git reset -q --hard origin/main || fallo "git reset falló"
    salida=$(python3 -I calcular.py 2>&1) || fallo "calcular.py falló: $(printf '%s' "$salida" | tail -3)"
    resumen=$(printf '%s' "$salida" | grep -E '^(Web|Ranking):' | tr '\n' ' ')
    anotar "$resumen"

    git add horas.json ranking.json
    if git diff --cached --quiet; then anotar "Sin cambios, no se publica"; informar ok "Sin cambios. $resumen"; exit 0; fi
    git commit -q -m "Horas al $(date '+%Y-%m-%d')" || fallo "git commit falló"
    if git push -q origin main; then
      anotar "Publicado"
      informar ok "Publicado. $resumen"
      exit 0
    fi
    [ "$intento" -ge 3 ] && fallo "git push rechazado 3 veces seguidas"
    anotar "git push rechazado (el PC habrá publicado a la vez). Reintento $((intento + 1)) de 3"
    intento=$((intento + 1))
    sleep 20
  done
}
main "$@"; exit $?
