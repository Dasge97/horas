"""Calcula las horas dedicadas a cada proyecto y escribe horas.json y ranking.json.

Dos fuentes:
  1. Los registros de sesiones de Claude Code (~/.claude/projects/*/*.jsonl). Cada
     mensaje lleva su hora. Se suma el tiempo entre mensajes y se descartan los
     huecos de más de 30 minutos. Es tiempo real de trabajo.
  2. Los commits de cada repositorio. Se agrupan los commits con menos de 2 horas
     entre ellos y se suma media hora por bloque. Es una estimación, y se queda
     corta en repos con pocos commits grandes.
     En los repos propios solo cuenta la rama principal (en los de Bitbucket, todas), y no cuenta los commits automáticos
     "Horas al ..." que crea la propia publicación.

Para no contar dos veces lo mismo, los bloques de commits que caen en un día con
sesiones de Claude Code para ese proyecto se descartan: ese día ya está medido
por la fuente 1.

Corre en dos máquinas distintas con el mismo código:
  - En el PC están los registros de Claude Code y `gh`. Aquí se calcula la fuente 1
    y se escriben sesiones.json (horas por proyecto y por carpeta) y repos.json
    (la lista de repositorios), que se suben al repositorio.
  - En el servidor no hay registros ni `gh`: la fuente 1 se lee de sesiones.json y
    la lista de repos de repos.json. Los commits se leen de copias desnudas de
    cada repo en espejos/, que se crean y actualizan con la clave SSH del servidor.

Salidas:
  - horas.json: solo los proyectos de daniunico.com. Lo lee la web.
  - ranking.json: todo, incluidos los repos privados y los que no están en la web.
    Lo lee el panel de horas.code-hive.space.
  - pendientes.json: local, no se publica. Repos y carpetas con horas que no están
    asignados a ningún proyecto de la web ni en la lista "ignorar".

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

SIN_SESIONES = {'horas': 0, 'dias': []}


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

# Commits que no son trabajo: los que crea la propia publicación de horas cada pocas horas.
COMMITS_AUTOMATICOS = re.compile(r'^Horas al \d{4}-\d{2}-\d{2}')


def instantes_commits_gh(repo):
    """Fechas de los commits de la rama principal, sin los automáticos. Igual que instantes_commits_espejo."""
    salida = subprocess.run(
        ['gh', 'api', f'repos/{USUARIO}/{repo}/commits?per_page=100', '--paginate',
         '--jq', '.[] | .commit.author.date + "\\t" + (.commit.message | split("\\n")[0])'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300,
    ).stdout
    instantes = []
    for linea in salida.splitlines():
        if '\t' not in linea or linea.startswith('{'):
            continue
        cuando, mensaje = linea.split('\t', 1)
        if COMMITS_AUTOMATICOS.match(mensaje):
            continue
        t = fecha(cuando.strip())
        if t:
            instantes.append(t)
    return instantes


def instantes_commits_espejo(repo, url=None, autores=None, todas_las_ramas=False):
    """Fechas de los commits de una copia desnuda. Con `autores`, solo los de esos correos.
    Por defecto solo la rama principal; con todas_las_ramas, todas (para los repos de trabajo)."""
    ruta = os.path.join(ESPEJOS, repo.replace('/', '__') + '.git')
    url = url or f'git@github.com:{USUARIO}/{repo}.git'
    if not os.path.isdir(ruta):
        os.makedirs(ESPEJOS, exist_ok=True)
        r = subprocess.run(['git', 'clone', '--quiet', '--bare', '--filter=blob:none', url, ruta],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        if r.returncode != 0:
            print(f'  aviso: no se pudo clonar {repo}: {r.stderr.strip()[:120]}', file=sys.stderr)
            return []
    else:
        # Una copia --bare no trae configurado qué ramas actualizar: sin la regla explícita,
        # "git fetch" no cambia nada y la copia se queda como el día que se clonó.
        r = subprocess.run(['git', '--git-dir', ruta, 'fetch', '--quiet', '--prune', 'origin',
                            '+refs/heads/*:refs/heads/*'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        if r.returncode != 0:
            print(f'  aviso: no se pudo actualizar {repo}: {r.stderr.strip()[:120]}', file=sys.stderr)
    # Repos propios: solo la rama principal (HEAD), igual que la API de GitHub en instantes_commits_gh,
    # para que el PC y el servidor cuenten lo mismo. Repos de trabajo (Bitbucket): todas las ramas, porque
    # allí se trabaja en ramas aparte; solo los calcula el PC y se filtran por autor.
    salida = subprocess.run(['git', '--git-dir', ruta, 'log', '--all' if todas_las_ramas else 'HEAD', '--format=%aI%x09%ae%x09%s'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120).stdout
    instantes = []
    for linea in salida.splitlines():
        partes = linea.split('\t', 2)
        if len(partes) < 2:
            continue
        cuando, correo = partes[0], partes[1]
        mensaje = partes[2] if len(partes) > 2 else ''
        if autores and correo.strip().lower() not in autores:
            continue
        if COMMITS_AUTOMATICOS.match(mensaje):
            continue
        t = fecha(cuando.strip())
        if t:
            instantes.append(t)
    return instantes


def instantes_commits_bitbucket(config, repo):
    bb = config['bitbucket']
    autores = {a.lower() for a in bb['autores']}
    return instantes_commits_espejo(f"bitbucket/{repo}", url=f"git@bitbucket.org:{bb['espacio']}/{repo}.git", autores=autores, todas_las_ramas=True)


_cache_commits = {}

def instantes_commits(repo):
    if repo not in _cache_commits:
        _cache_commits[repo] = instantes_commits_gh(repo) if HAY_GH else instantes_commits_espejo(repo)
    return _cache_commits[repo]


def lista_repos():
    """{nombre: privado} de los repos propios, sin forks. Con gh se consulta y se guarda en repos.json; sin gh se lee."""
    ruta = os.path.join(AQUI, 'repos.json')
    if HAY_GH:
        datos = json.loads(subprocess.run(['gh', 'repo', 'list', USUARIO, '--limit', '300', '--json', 'name,isPrivate,isFork'],
                                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120).stdout)
        repos = {r['name']: r['isPrivate'] for r in sorted(datos, key=lambda r: r['name']) if not r['isFork']}
        with open(ruta, 'w', encoding='utf-8') as fh:
            json.dump(repos, fh, ensure_ascii=False, indent=1)
        return repos
    if os.path.exists(ruta):
        return json.load(open(ruta, encoding='utf-8'))
    print('aviso: no hay gh ni repos.json; solo se miden los repos de proyectos.json', file=sys.stderr)
    return None


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


def escribir(nombre, documento):
    with open(os.path.join(AQUI, nombre), 'w', encoding='utf-8') as fh:
        json.dump(documento, fh, ensure_ascii=False, indent=1)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    config = json.load(open(os.path.join(AQUI, 'proyectos.json'), encoding='utf-8'))
    ahora = datetime.now(timezone.utc).isoformat(timespec='seconds')
    print(f"Fuentes: sesiones {'de los registros locales' if HAY_REGISTROS else 'de sesiones.json'}, "
          f"commits {'por la API de GitHub (gh)' if HAY_GH else 'de las copias en espejos/'}")

    # Fuente 1: por proyecto de la web y por carpeta sin asignar
    carpetas_de_proyectos = {c for p in config['proyectos'] for c in p['carpetas']}
    if HAY_REGISTROS:
        carpetas = carpetas_registradas()
        sesiones = {p['clave']: resumen_sesiones(sum((instantes_sesiones(carpetas[c]) for c in p['carpetas'] if c in carpetas), []))
                    for p in config['proyectos']}
        sesiones_carpetas = {c: resumen_sesiones(instantes_sesiones(carpetas[c])) for c in sorted(set(carpetas) - carpetas_de_proyectos)}
        sesiones_carpetas = {c: s for c, s in sesiones_carpetas.items() if s['horas'] > 0}
        escribir('sesiones.json', {'actualizado': ahora, 'proyectos': sesiones, 'carpetas': sesiones_carpetas})
    else:
        ruta = os.path.join(AQUI, 'sesiones.json')
        guardado = json.load(open(ruta, encoding='utf-8')) if os.path.exists(ruta) else {}
        sesiones = guardado.get('proyectos', {})
        sesiones_carpetas = guardado.get('carpetas', {})

    # Fuente 2 y combinación, proyectos de la web
    repos = lista_repos()
    proyectos, usados, todos_los_dias = {}, set(), set()
    for p in config['proyectos']:
        commits = []
        for r in p['repos']:
            if repos is None or r in repos:
                commits += instantes_commits(r)
                usados.add(r)
        medida = medir(sesiones.get(p['clave'], SIN_SESIONES), commits)
        todos_los_dias |= medida.pop('_dias')
        proyectos[p['clave']] = {'nombre': p['nombre'], **medida}
        print(f"{p['nombre'][:34]:34} {medida['horas']:7.1f} h  {medida['dias']:4d} días")

    metodo = ('Sesiones de Claude Code (huecos de más de 30 min no cuentan) más bloques de commits '
              '(huecos de menos de 2 h, más 30 min por bloque) en los días sin sesiones registradas.')
    escribir('horas.json', {
        'actualizado': ahora, 'metodo': metodo,
        'total_horas': round(sum(v['horas'] for v in proyectos.values()), 1),
        'total_dias': len(todos_los_dias),
        'proyectos': proyectos,
    })
    print(f"\nWeb: {round(sum(v['horas'] for v in proyectos.values()), 1)} h en {len(todos_los_dias)} días · escrito en horas.json")

    # Ranking: todo lo que tiene horas, esté o no en la web
    claves_repos = {r: p['clave'] for p in config['proyectos'] for r in p['repos']}
    entradas = []
    for p in config['proyectos']:
        v = proyectos[p['clave']]
        if v['horas'] > 0:
            entradas.append({'nombre': p['nombre'], 'tipo': 'proyecto', 'en_web': True,
                             'privado': any(repos.get(r, False) for r in p['repos']) if repos else False,
                             'repos': p['repos'], **{k: v[k] for k in ('horas', 'horas_sesiones', 'horas_commits', 'dias', 'commits', 'desde', 'hasta')}})
    dias_ranking = set(todos_los_dias)
    de_trabajo = set(config.get('trabajo', {}).get('repos', []))
    if repos:
        for r in sorted(repos):
            if r in usados:
                continue
            m = medir(SIN_SESIONES, instantes_commits(r))
            dias_ranking |= m.pop('_dias')
            if m['horas'] > 0:
                entradas.append({'nombre': r, 'tipo': 'trabajo' if r in de_trabajo else 'repo', 'en_web': False,
                                 'privado': repos[r], 'repos': [r], **m})
    for c, s in sesiones_carpetas.items():
        m = medir(s, [])
        dias_ranking |= m.pop('_dias')
        entradas.append({'nombre': c, 'tipo': 'carpeta', 'en_web': False, 'privado': True, 'repos': [], **m})
    # Repos del trabajo en Bitbucket: solo los commits propios. Los calcula el PC,
    # que tiene acceso a Bitbucket, y los guarda en bitbucket.json; el servidor
    # lee ese fichero.
    ruta_bb = os.path.join(AQUI, 'bitbucket.json')
    if HAY_GH:
        trabajo = {}
        for r in config.get('bitbucket', {}).get('repos', []):
            m = medir(SIN_SESIONES, instantes_commits_bitbucket(config, r))
            trabajo[r] = {**m, '_dias': sorted(d.isoformat() for d in m['_dias'])}
        escribir('bitbucket.json', {'actualizado': ahora, 'repos': {r: {k: v for k, v in m.items() if k != '_dias'} | {'dias_lista': m['_dias']} for r, m in trabajo.items()}})
    else:
        guardado = json.load(open(ruta_bb, encoding='utf-8')) if os.path.exists(ruta_bb) else {'repos': {}}
        trabajo = {r: {**m, '_dias': m.get('dias_lista', [])} for r, m in guardado['repos'].items()}
    for r, m in trabajo.items():
        dias_ranking |= {datetime.fromisoformat(d).date() for d in m.pop('_dias')}
        m.pop('dias_lista', None)
        if m['horas'] > 0:
            entradas.append({'nombre': r, 'tipo': 'trabajo', 'en_web': False, 'privado': True, 'repos': [f'bitbucket:{r}'], **m})
            print(f"{r[:34]:34} {m['horas']:7.1f} h  {m['dias']:4d} días  (Bitbucket, solo commits propios)")
    entradas.sort(key=lambda e: -e['horas'])
    escribir('ranking.json', {
        'actualizado': ahora, 'metodo': metodo,
        'total_horas': round(sum(e['horas'] for e in entradas), 1),
        'total_dias': len(dias_ranking),
        'entradas': entradas,
    })
    print(f"Ranking: {round(sum(e['horas'] for e in entradas), 1)} h en {len(dias_ranking)} días, {len(entradas)} entradas · escrito en ranking.json")

    # Pendientes de asignar
    if repos:
        ignorar = set(config.get('ignorar', []))
        pendientes = {
            'repos': {e['nombre']: e['horas'] for e in entradas if e['tipo'] == 'repo' and e['nombre'] not in ignorar and e['nombre'] not in de_trabajo},
            'carpetas': {e['nombre']: e['horas'] for e in entradas if e['tipo'] == 'carpeta' and e['nombre'] not in ignorar},
        }
        escribir('pendientes.json', pendientes)
        print(f"Sin asignar: {len(pendientes['repos'])} repos y {len(pendientes['carpetas'])} carpetas, en pendientes.json")


if __name__ == '__main__':
    main()
