# Calcula las horas y publica horas.json en GitHub. Lo ejecuta la tarea programada
# cada noche. Si algo falla, lo apunta en registro.log y, si la bóveda de codehive
# está desbloqueada, avisa al móvil por ntfy.

$ErrorActionPreference = 'Stop'
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
    $salida = & python -I (Join-Path $aqui 'calcular.py') 2>&1
    if ($LASTEXITCODE -ne 0) { throw "calcular.py terminó con código $LASTEXITCODE`n$salida" }
    $ultima = ($salida | Select-String '^Total:' | Select-Object -Last 1).Line
    Anotar $ultima

    & git add horas.json
    $cambios = & git status --porcelain horas.json
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
