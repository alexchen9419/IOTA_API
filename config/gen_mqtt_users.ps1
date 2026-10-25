# Generate MQTT users: missing passwords are created and appended to the repo-root .env,
# then config/passwd is rebuilt with mosquitto_passwd (run inside docker).
# Safe to re-run; passwords already in .env are kept.
#
# Usage (PowerShell, repo root):   .\config\gen_mqtt_users.ps1
# Then restart the broker:         docker compose up -d --force-recreate mqtt-broker
#
# Passwords live only in .env and in the hashed config/passwd (both are gitignored).
# NOTE: keep this file ASCII-only - Windows PowerShell 5.1 misreads BOM-less UTF-8.
$ErrorActionPreference = 'Stop'
$root    = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) { New-Item -ItemType File $envFile | Out-Null }

function Get-EnvValue($name) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^$name=(.+)$" } | Select-Object -First 1
    if ($line) { return ($line -split '=', 2)[1] }
    return $null
}
function New-Secret {
    $b = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return (($b | ForEach-Object { '{0:x2}' -f $_ }) -join '')
}
foreach ($name in 'MQTT_SERVER_PASSWORD', 'MQTT_NODERED_PASSWORD', 'MQTT_DEVICE_PASSWORD', 'NODE_RED_ADMIN_PASSWORD') {
    if (-not (Get-EnvValue $name)) {
        Add-Content -Path $envFile -Encoding UTF8 -Value "$name=$(New-Secret)"
        Write-Host "Generated $name in .env"
    }
}

$users = [ordered]@{
    'server'  = Get-EnvValue 'MQTT_SERVER_PASSWORD'
    'nodered' = Get-EnvValue 'MQTT_NODERED_PASSWORD'
    'device'  = Get-EnvValue 'MQTT_DEVICE_PASSWORD'
}
$cfg = Join-Path $root 'config'
$pwFile = Join-Path $cfg 'passwd'
if (Test-Path $pwFile) { Remove-Item $pwFile -Force }   # -c refuses to overwrite an existing file
$create = @('-c')   # first user creates the file, the rest append
foreach ($u in $users.Keys) {
    docker run --rm -v "${cfg}:/c" eclipse-mosquitto mosquitto_passwd -b @create /c/passwd $u $users[$u]
    if ($LASTEXITCODE -ne 0) { throw "mosquitto_passwd failed for $u" }
    $create = @()
}
# mosquitto 2.1 runs as uid 1883 and refuses a password file it cannot read; the helper
# container created it as root:0600, so hand it over to the mosquitto user.
docker run --rm --entrypoint sh -v "${cfg}:/c" eclipse-mosquitto -c "chown 1883:1883 /c/passwd && chmod 600 /c/passwd"
if ($LASTEXITCODE -ne 0) { throw "failed to set permissions on config/passwd" }
Write-Host ("config/passwd updated (users: " + ($users.Keys -join ', ') + ")")
