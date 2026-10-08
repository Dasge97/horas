"""Calcula las horas dedicadas a cada proyecto de daniunico.com y escribe horas.json.

Dos fuentes:
  1. Los registros de sesiones de Claude Code (~/.claude/projects/*/*.jsonl). Cada
     mensaje lleva su hora. Se suma el tiempo entre mensajes y se descartan los
     huecos de más de 30 minutos. Es tiempo real de trabajo.
  2. Los commits de cada repositorio. Se agrupan los commits con menos de 2 horas
     entre ellos y se suma media hora por bloque. Es una estimación, y se queda
     corta en repos con pocos commits grandes.

Para no contar dos veces lo mismo, los bloques de commits que caen en un día con
sesiones de Claude Code para ese proyecto se descartan: ese día ya está medido
por la fuente 1.

Corre en dos máquinas distintas con el mismo código:
  - En el PC están los registros de Claude Code. Aquí se calcula la fuente 1 y se
    escribe sesiones.json, que se sube al repositorio.
  - En el servidor no hay registros: la fuente 1 se lee de sesiones.json. Los
    commits se leen de copias desnudas de cada repo en espejos/, que se crean y
    actualizan con la clave SSH del servidor. Si hay `gh` instalado, se usa la API
    de GitHub en su lugar.

Los repos y carpetas que no estén en proyectos.json (ni en su lista "ignorar")
se escriben en pendientes.json, local y sin publicar, para ver qué falta por
asignar. Solo se calcula donde hay `gh`, porque hace falta la lista de repos.

Uso: python3 -I calcular.py
"""
import glob, json, os, re, shutil, subprocess, sys
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
REGISTROS = os.path.expanduser('~/.claude/projects')
ESPEJOS = os.path.join(AQUI, 'espejos')
USUARIO = 'Dasge97'
PREFIJO_CARPETA = 'c--AreaDeTrabajo-'
HUECO_SESION = timedelta(minutes=30)
HUECO_COMMITS = timedelta(hours=2)
ARRANQUE_COMMITS = timedelta(minutes=30)
HAY_GH = shutil.which('gh') is not None
# Solo cuentan los registros si hay carpetas de AreaDeTrabajo: el servidor también
# tiene ~/.claude/projects, pero con sus propias carpetas, y no debe pisar sesiones.json.
HAY_REGISTROS = os.path.isdir(REGISTROS) and any(
    os.path.basename(r).lower().startswith(PREFIJO_CARPETA.lower()) for r in glob.glob(os.path.join(REGISTROS, '*')))


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


# --- Fuente 1: sesiones de Claude Code ---

def carpetas_registradas():
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


def resumen_sesiones(instantes):
    horas = sum(((fin - ini) for ini, fin in bloques(instantes, HUECO_SESION)), timedelta(0))
    return {'horas': round(horas.total_seconds() / 3600, 2), 'dias': sorted({t.date().isoformat() for t in instantes})}


# --- Fuente 2: commits ---

def instantes_commits_gh(repo):
    salida = subprocess.run(
        ['gh', 'api', f'repos/{USUARIO}/{repo}/commits?per_page=100', '--paginate', '--jq', '.[].commit.author.date'],
        capture_output=True, text=True, timeout=300,
    ).stdout
    return [t for t in (fecha(l.strip()) for l in salida.splitlines() if l.strip() and not l.startswith('{')) if t]


def instantes_commits_espejo(repo):
    ruta = os.path.join(ESPEJOS, repo + '.git')
    url = f'git@github.com:{USUARIO}/{repo}.git'
    if not os.path.isdir(ruta):
        os.makedirs(ESPEJOS, exist_ok=True)
        r = subprocess.run(['git', 'clone', '--quiet', '--bare', '--filter=blob:none', url, ruta],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print(f'  aviso: no se pudo clonar {repo}: {r.stderr.strip()[:120]}', file=sys.stderr)
            return []
    else:
        subprocess.run(['git', '--git-dir', ruta, 'fetch', '--quiet', '--all', '--prune'],
                       capture_output=True, text=True, timeout=600)
    salida = subprocess.run(['git', '--git-dir', ruta, 'log', '--all', '--format=%aI'],
                            capture_output=True, text=True, timeout=120).stdout
    return [t for t in (fecha(l.strip()) for l in salida.splitlines() if l.strip()) if t]


def instantes_commits(repo):
    return instantes_commits_gh(repo) if HAY_GH else instantes_commits_espejo(repo)


def repos_existentes():
    """Lista de repos propios (sin forks). Solo con gh; sin gh se confía en proyectos.json."""
    if not HAY_GH:
        return None
    datos = json.loads(subprocess.run(['gh', 'repo', 'list', USUARIO, '--limit', '300', '--json', 'name,isFork'],
                                      capture_output=True, text=True, timeout=120).stdout)
    return {r['name'] for r in datos if not r['isFork']}


# --- Combinación ---

def medir(sesiones, commits):
    """`sesiones` es el resumen {'horas', 'dias'}; `commits` la lista de instantes."""
    dias_sesiones = {datetime.fromisoformat(d).date() for d in sesiones['dias']}
    t_commits = timedelta(0)
    for ini, fin in bloques(commits, HUECO_COMMITS):
        if ini.date() in dias_sesiones or fin.date() in dias_sesiones:
            continue
        t_commits += (fin - ini) + ARRANQUE_COMMITS
    dias = dias_sesiones | {t.date() for t in commits}
    horas_commits = t_commits.total_seconds() / 3600
    return {
        'horas': round(sesiones['horas'] + horas_commits, 1),
        'horas_sesiones': round(sesiones['horas'], 1),
        'horas_commits': round(horas_commits, 1),
        'dias': len(dias),
        'commits': len(commits),
        'desde': min(dias).isoformat() if dias else None,
        'hasta': max(dias).isoformat() if dias else None,
        '_dias': dias,
    }


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    config = json.load(open(os.path.join(AQUI, 'proyectos.json'), encoding='utf-8'))
    ruta_sesiones = os.path.join(AQUI, 'sesiones.json')
    print(f"Fuentes: sesiones {'de los registros locales' if HAY_REGISTROS else 'de sesiones.json'}, "
          f"commits {'por la API de GitHub (gh)' if HAY_GH else 'de las copias en espejos/'}")

    # Fuente 1
    if HAY_REGISTROS:
        carpetas = carpetas_registradas()
        sesiones = {}
        for p in config['proyectos']:
            instantes = []
            for c in p['carpetas']:
                if c in carpetas:
                    instantes += instantes_sesiones(carpetas[c])
            sesiones[p['clave']] = resumen_sesiones(instantes)
        with open(ruta_sesiones, 'w', encoding='utf-8') as fh:
            json.dump({'actualizado': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'proyectos': sesiones},
                      fh, ensure_ascii=False, indent=1)
    else:
        carpetas = {}
        sesiones = json.load(open(ruta_sesiones, encoding='utf-8'))['proyectos'] if os.path.exists(ruta_sesiones) else {}

    # Fuente 2 y combinación
    repos = repos_existentes()
    proyectos, usados, todos_los_dias = {}, set(), set()
    for p in config['proyectos']:
        commits = []
        for r in p['repos']:
            if repos is None or r in repos:
                commits += instantes_commits(r)
                usados.add(r)
        medida = medir(sesiones.get(p['clave'], {'horas': 0, 'dias': []}), commits)
        todos_los_dias |= medida.pop('_dias')
        proyectos[p['clave']] = {'nombre': p['nombre'], **medida}
        print(f"{p['nombre'][:34]:34} {medida['horas']:7.1f} h  {medida['dias']:4d} días")

    documento = {
        'actualizado': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'metodo': 'Sesiones de Claude Code (huecos de más de 30 min no cuentan) más bloques de commits (huecos de menos de 2 h, más 30 min por bloque) en los días sin sesiones registradas.',
        'total_horas': round(sum(v['horas'] for v in proyectos.values()), 1),
        'total_dias': len(todos_los_dias),
        'proyectos': proyectos,
    }
    with open(os.path.join(AQUI, 'horas.json'), 'w', encoding='utf-8') as fh:
        json.dump(documento, fh, ensure_ascii=False, indent=1)
    print(f"\nTotal: {documento['total_horas']} h en {documento['total_dias']} días · escrito en horas.json")

    # Pendientes de asignar, solo donde se puede listar todo
    if repos is not None:
        ignorar = set(config.get('ignorar', []))
        pendientes = {'repos': {}, 'carpetas': {}}
        for r in sorted(repos - usados - ignorar):
            m = medir({'horas': 0, 'dias': []}, instantes_commits(r))
            pendientes['repos'][r] = m['horas']
        usadas_carpetas = {c for p in config['proyectos'] for c in p['carpetas']}
        for c in sorted(set(carpetas) - usadas_carpetas - ignorar):
            m = resumen_sesiones(instantes_sesiones(carpetas[c]))
            if m['horas'] > 0:
                pendientes['carpetas'][c] = round(m['horas'], 1)
        with open(os.path.join(AQUI, 'pendientes.json'), 'w', encoding='utf-8') as fh:
            json.dump(pendientes, fh, ensure_ascii=False, indent=1)
        print(f"Sin asignar: {len(pendientes['repos'])} repos y {len(pendientes['carpetas'])} carpetas, en pendientes.json")


if __name__ == '__main__':
    main()
