# Versión para el PC. Calcula las horas con los registros de Claude Code y sube
# sesiones.json y horas.json a GitHub. Se ejecuta cuando el PC está encendido; la
# publicación nocturna fiable la hace publicar.sh en codehive. Si algo falla, lo
# apunta en registro.log y, si la bóveda de codehive está desbloqueada, avisa por ntfy.

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

function Avisar($titulo, $texto) {
    # Mejor esfuerzo: necesita la bóveda desbloqueada en codehive. Si no, se calla.
    try {
        $orden = "/home/codehive/bin/vault-usar `"ntfy - token scripts`" NTFY_TOKEN -- sh -c 'curl -s -H `"Authorization: Bearer `$NTFY_TOKEN`" -H `"Title: $titulo`" -H `"Tags: hourglass`" -d `"$texto`" https://ntfy.code-hive.space/claude'"
        ssh -o ConnectTimeout=10 -o BatchMode=yes codehive $orden | Out-Null
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

    & git add horas.json ranking.json sesiones.json repos.json bitbucket.json
    $cambios = & git status --porcelain horas.json ranking.json sesiones.json repos.json bitbucket.json
    if (-not $cambios) { Anotar 'Sin cambios, no se publica'; exit 0 }

    & git commit -q -m "Horas al $(Get-Date -Format 'yyyy-MM-dd')" | Out-Null
    & git push -q origin main
    if ($LASTEXITCODE -ne 0) { throw 'git push falló' }
    Anotar 'Publicado'
} catch {
    Anotar "ERROR: $($_.Exception.Message)"
    Avisar 'horas: fallo al publicar' "$($_.Exception.Message)"
    exit 1
}
