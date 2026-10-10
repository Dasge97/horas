# Versión para el PC. Calcula las horas con los registros de Claude Code y sube
# sesiones.json y horas.json a GitHub. Se ejecuta cuando el PC está encendido; la
# publicación nocturna fiable la hace publicar.sh en codehive. Todo queda en registro.log.
# El resultado se informa al Kit en su estado.json: el panel lo enseña y, si falla, el Kit avisa al móvil.

$ErrorActionPreference = 'Stop'
# Python escribe en UTF-8; sin esto PowerShell lo lee en la página de códigos de Windows y rompe los acentos.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$registro = Join-Path $aqui 'registro.log'
Set-Location $aqui

function Anotar($texto) {
    $linea = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $texto"
    Add-Content -Path $registro -Value $linea -Encoding utf8
    Write-Output $linea
}

# Escribe el estado.json que lee KitDani, en la ruta que el Kit dejó en estado-ruta.txt al importar
# la herramienta. Sin ese fichero no hace nada. Nunca hace fallar la publicación.
function Informar($estado, $mensaje, $indicadores = @()) {
    try {
        $rutaFichero = Join-Path $aqui 'estado-ruta.txt'
        if (-not (Test-Path $rutaFichero)) { return }
        $destino = (Get-Content $rutaFichero -TotalCount 1).Trim()
        if (-not $destino) { return }
        $datos = [ordered]@{
            estado      = $estado
            actualizado = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
            mensaje     = $mensaje
            indicadores = @($indicadores)
        }
        $temporal = "$destino.tmp"
        New-Item -ItemType Directory -Force -Path (Split-Path $destino) | Out-Null
        [IO.File]::WriteAllText($temporal, ($datos | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
        Move-Item -Force $temporal $destino
    } catch {}
}

try {
    Anotar 'Inicio'
    & git pull -q --rebase origin main
    if ($LASTEXITCODE -ne 0) { throw 'git pull falló' }
    $salida = & python -I (Join-Path $aqui 'calcular.py') 2>&1
    if ($LASTEXITCODE -ne 0) { throw "calcular.py terminó con código $LASTEXITCODE`n$salida" }
    $ultima = (($salida | Select-String '^(Web|Ranking):') | ForEach-Object { $_.Line }) -join ' '
    Anotar $ultima
    $horasWeb = if ($ultima -match 'Web: ([\d.]+) h') { $Matches[1] } else { '?' }
    $indicadores = @(
        [ordered]@{ etiqueta = 'Horas totales'; valor = $horasWeb },
        [ordered]@{ etiqueta = 'Última publicación'; valor = (Get-Date -Format 'HH:mm') }
    )

    & git add horas.json ranking.json sesiones.json repos.json bitbucket.json
    $cambios = & git status --porcelain horas.json ranking.json sesiones.json repos.json bitbucket.json
    if (-not $cambios) {
        Anotar 'Sin cambios, no se publica'
        Informar 'ok' 'Sin cambios que publicar' $indicadores
        exit 0
    }

    & git commit -q -m "Horas al $(Get-Date -Format 'yyyy-MM-dd')" | Out-Null
    & git push -q origin main
    if ($LASTEXITCODE -ne 0) { throw 'git push falló' }
    Anotar 'Publicado'
    Informar 'ok' 'Publicado' $indicadores
} catch {
    Anotar "ERROR: $($_.Exception.Message)"
    Informar 'error' "Fallo al publicar: $($_.Exception.Message)"
    exit 1
}
