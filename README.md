# horas

Cuántas horas he dedicado a cada proyecto de [daniunico.com](https://daniunico.com). Se calcula solo, cada noche, y la web lo lee de aquí.

El dato público es [`horas.json`](horas.json). La web lo carga desde
`https://raw.githubusercontent.com/Dasge97/horas/main/horas.json` al abrirse, así que no hay que recompilar ni desplegar nada para que cambie.

## Cómo se mide

Dos fuentes, combinadas por proyecto:

1. **Sesiones de Claude Code.** Cada mensaje de una sesión lleva su hora. Se suma el tiempo entre mensajes y se descartan los huecos de más de 30 minutos. Es tiempo real de trabajo. Solo hay registros desde finales de agosto de 2026.
2. **Commits de GitHub.** Se agrupan los commits con menos de dos horas entre ellos y se suma media hora por cada bloque. Es una estimación, y se queda corta en repositorios con pocos commits grandes.

Para no contar dos veces el mismo trabajo, los bloques de commits que caen en un día con sesiones registradas de ese proyecto se descartan.

Ninguna de las dos fuentes mide el tiempo pensando, dibujando o probando sin tocar el teclado. El número real es mayor.

## Ficheros

| Fichero | Qué es |
|---|---|
| `proyectos.json` | Qué repositorios y qué carpetas de sesiones forman cada proyecto de la web. Es lo único que se edita a mano. |
| `calcular.py` | Lee las dos fuentes y escribe `horas.json`. |
| `horas.json` | El resultado publicado. |
| `publicar.ps1` | Ejecuta el cálculo, hace commit y push. Si falla, lo apunta en `registro.log` y avisa al móvil. |
| `pendientes.json` | Local, no se publica. Repositorios y carpetas con horas que aún no están asignados a ningún proyecto. |

## Formato de horas.json

```json
{
  "actualizado": "2026-10-09T02:30:00+00:00",
  "total_horas": 421.3,
  "total_dias": 123,
  "proyectos": {
    "card-pop": {
      "nombre": "card-pop",
      "horas": 19.4,
      "horas_sesiones": 17.1,
      "horas_commits": 2.3,
      "dias": 4,
      "commits": 112,
      "desde": "2026-10-02",
      "hasta": "2026-10-07"
    }
  }
}
```

La clave de cada proyecto es el `slug` de la ficha o del juego en la web, o el nombre tal cual en las secciones de otros sistemas y herramientas.

## Cuando entra un proyecto nuevo

1. Se añade a la web, en `src/data/contenido.js` de daniunico-web.
2. Se añade una línea a `proyectos.json` con la misma clave, sus repositorios y sus carpetas de sesiones.
3. A la noche siguiente aparece con sus horas.

Mientras no se haga el paso 2, el repositorio sale en `pendientes.json` con las horas que lleva. Los repositorios que no van a ir nunca a la web se ponen en la lista `ignorar` de `proyectos.json`.

## Puesta en marcha

Hace falta Python 3 y `gh` con sesión iniciada.

```powershell
python -I calcular.py        # calcula y escribe horas.json
.\publicar.ps1               # calcula, commit y push
```

Para que se ejecute solo cada noche, registrar la tarea de Windows una vez, desde PowerShell en esta carpeta:

```powershell
$script = (Resolve-Path .\publicar.ps1).Path
$accion = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory (Get-Location).Path
$disparador = New-ScheduledTaskTrigger -Daily -At 03:30
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName 'Horas de proyectos (daniunico.com)' -Action $accion -Trigger $disparador -Settings $ajustes -Force
```

`-StartWhenAvailable` hace que, si el PC estaba apagado a las 03:30, la tarea se ejecute al encenderlo.

El aviso al móvil usa el servidor ntfy propio a través de la bóveda de codehive. Si la bóveda está bloqueada, el aviso se omite y el error queda solo en `registro.log`.
