"""Calcula las horas dedicadas a cada proyecto de daniunico.com y escribe horas.json.

Dos fuentes:
  1. Los registros de sesiones de Claude Code (~/.claude/projects/*/*.jsonl). Cada
     mensaje lleva su hora. Se suma el tiempo entre mensajes y se descartan los
     huecos de más de 30 minutos. Es tiempo real de trabajo.
  2. Los commits de cada repositorio en GitHub, leídos con `gh`. Se agrupan los
     commits con menos de 2 horas entre ellos y se suma media hora por bloque.
     Es una estimación, y se queda corta en repos con pocos commits grandes.

Para no contar dos veces lo mismo, los bloques de commits que caen en un día con
sesiones de Claude Code para ese proyecto se descartan: ese día ya está medido
por la fuente 1.

Los repos y carpetas que no estén en proyectos.json (ni en su lista "ignorar")
se escriben en pendientes.json, un fichero local que no se publica, con sus
horas, para ver qué falta por asignar. horas.json solo lleva los proyectos de la web.

Uso: python -I calcular.py [ruta de salida]   (por defecto, horas.json junto al script)
"""
import glob, json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
REGISTROS = os.path.expanduser('~/.claude/projects')
USUARIO = 'Dasge97'
PREFIJO_CARPETA = 'c--AreaDeTrabajo-'
HUECO_SESION = timedelta(minutes=30)
HUECO_COMMITS = timedelta(hours=2)
ARRANQUE_COMMITS = timedelta(minutes=30)


def fecha(texto):
    try:
        return datetime.fromisoformat(texto.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def bloques(instantes, hueco):
    """Agrupa instantes en bloques separados por más de `hueco`. Devuelve [(inicio, fin)]."""
    instantes = sorted(set(instantes))
    resultado = []
    if not instantes:
        return resultado
    inicio = anterior = instantes[0]
    for t in instantes[1:]:
        if t - anterior > hueco:
            resultado.append((inicio, anterior))
            inicio = t
        anterior = t
    resultado.append((inicio, anterior))
    return resultado


def carpetas_registradas():
    """Nombre corto de cada carpeta de registros de Claude Code dentro de AreaDeTrabajo."""
    nombres = {}
    for ruta in glob.glob(os.path.join(REGISTROS, '*')):
        base = os.path.basename(ruta)
        if base.lower().startswith(PREFIJO_CARPETA.lower()) and len(base) > len(PREFIJO_CARPETA):
            nombres[base[len(PREFIJO_CARPETA):]] = ruta
    return nombres


def instantes_sesiones(ruta_carpeta):
    instantes = []
    for fichero in glob.glob(os.path.join(ruta_carpeta, '*.jsonl')):
        with open(fichero, encoding='utf-8', errors='ignore') as fh:
            for linea in fh:
                m = re.search(r'"timestamp":"([^"]+)"', linea)
                if m:
                    t = fecha(m.group(1))
                    if t:
                        instantes.append(t)
    return instantes


def instantes_commits(repo):
    salida = subprocess.run(
        ['gh', 'api', f'repos/{USUARIO}/{repo}/commits?per_page=100', '--paginate', '--jq', '.[].commit.author.date'],
        capture_output=True, text=True, timeout=300,
    ).stdout
    return [t for t in (fecha(l.strip()) for l in salida.splitlines() if l.strip() and not l.startswith('{')) if t]


def medir(sesiones, commits):
    """Combina las dos fuentes. Devuelve un diccionario listo para el JSON."""
    t_sesiones = sum(((fin - ini) for ini, fin in bloques(sesiones, HUECO_SESION)), timedelta(0))
    dias_sesiones = {t.date() for t in sesiones}
    t_commits = timedelta(0)
    for ini, fin in bloques(commits, HUECO_COMMITS):
        if ini.date() in dias_sesiones or fin.date() in dias_sesiones:
            continue
        t_commits += (fin - ini) + ARRANQUE_COMMITS
    dias = dias_sesiones | {t.date() for t in commits}
    return {
        'horas': round((t_sesiones + t_commits).total_seconds() / 3600, 1),
        'horas_sesiones': round(t_sesiones.total_seconds() / 3600, 1),
        'horas_commits': round(t_commits.total_seconds() / 3600, 1),
        'dias': len(dias),
        'commits': len(commits),
        'desde': min(dias).isoformat() if dias else None,
        'hasta': max(dias).isoformat() if dias else None,
        '_dias': dias,
    }


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    salida = sys.argv[1] if len(sys.argv) > 1 else os.path.join(AQUI, 'horas.json')
    config = json.load(open(os.path.join(AQUI, 'proyectos.json'), encoding='utf-8'))
    carpetas = carpetas_registradas()
    repos_github = {r['name'] for r in json.loads(subprocess.run(
        ['gh', 'repo', 'list', USUARIO, '--limit', '300', '--json', 'name,isFork'],
        capture_output=True, text=True, timeout=120).stdout) if not r['isFork']}

    proyectos = {}
    usados_repos, usadas_carpetas = set(), set()
    todos_los_dias = set()
    for p in config['proyectos']:
        sesiones = []
        for c in p['carpetas']:
            if c in carpetas:
                sesiones += instantes_sesiones(carpetas[c])
                usadas_carpetas.add(c)
        commits = []
        for r in p['repos']:
            if r in repos_github:
                commits += instantes_commits(r)
                usados_repos.add(r)
        medida = medir(sesiones, commits)
        todos_los_dias |= medida.pop('_dias')
        proyectos[p['clave']] = {'nombre': p['nombre'], **medida}
        print(f"{p['nombre'][:34]:34} {medida['horas']:7.1f} h  {medida['dias']:4d} días")

    ignorar = set(config.get('ignorar', []))
    pendientes = {'repos': {}, 'carpetas': {}}
    for r in sorted(repos_github - usados_repos - ignorar):
        m = medir([], instantes_commits(r)); m.pop('_dias')
        pendientes['repos'][r] = m['horas']
    for c in sorted(set(carpetas) - usadas_carpetas - ignorar):
        m = medir(instantes_sesiones(carpetas[c]), []); m.pop('_dias')
        if m['horas'] > 0:
            pendientes['carpetas'][c] = m['horas']

    documento = {
        'actualizado': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'metodo': 'Sesiones de Claude Code (huecos de más de 30 min no cuentan) más bloques de commits (huecos de menos de 2 h, más 30 min por bloque) en los días sin sesiones registradas.',
        'total_horas': round(sum(v['horas'] for v in proyectos.values()), 1),
        'total_dias': len(todos_los_dias),
        'proyectos': proyectos,
    }
    with open(salida, 'w', encoding='utf-8') as fh:
        json.dump(documento, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(AQUI, 'pendientes.json'), 'w', encoding='utf-8') as fh:
        json.dump(pendientes, fh, ensure_ascii=False, indent=1)
    print(f"\nTotal: {documento['total_horas']} h en {documento['total_dias']} días · escrito en {salida}")
    print(f"Sin asignar: {len(pendientes['repos'])} repos y {len(pendientes['carpetas'])} carpetas, en pendientes.json")


if __name__ == '__main__':
    main()
