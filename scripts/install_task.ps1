# Installe (ou met à jour) la tâche planifiée Windows qui lance le run quotidien.
#   18:00 : run normal (après la clôture d'Euronext à 17:30)
#   20:30 : rattrapage — ne fait rien si le run de 18:00 a réussi (le run est idempotent)
# Tous les jours : les week-ends et jours fériés, le run passe en mode veille.
#
# Usage : powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
# Retrait : Unregister-ScheduledTask -TaskName "PEA sim - run quotidien" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot                       # dossier pea_sim
$python = Join-Path (Split-Path -Parent $root) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python introuvable : $python" }

$action = New-ScheduledTaskAction -Execute $python -Argument "run_daily.py" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At "18:00"),
    (New-ScheduledTaskTrigger -Daily -At "20:30")
)
# StartWhenAvailable : si le PC était éteint ou en veille à l'heure prévue, la tâche part dès que possible.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
# Session interactive : nécessaire pour accéder aux identifiants Claude et GitHub de l'utilisateur.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "PEA sim - run quotidien" -Description "Simulation PEA multi-agents" `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName "PEA sim - run quotidien" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
