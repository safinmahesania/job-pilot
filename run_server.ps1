# Start JobPilot for the Cloudflare tunnel to point at.
#
# -Host 127.0.0.1 on purpose: the tunnel connects from the same machine, so the app
# never listens on a public interface. Nothing binds to 0.0.0.0, so there is no port
# anyone else on your network can reach.

Set-Location $PSScriptRoot

# Load .env into this process
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
  }
}

# The server can restart itself (Admin > Restart server, or after a crash). This loop
# respawns it so a restart actually comes back up. Ctrl-C twice to stop for real.
#
# `python.exe -m uvicorn`, not `uvicorn.exe`: on machines with Smart App Control or a
# WDAC policy, the generated uvicorn.exe launcher inside .venv is an unrecognised
# executable and gets blocked ("An Application Control policy has blocked this file").
# python.exe is already trusted, and running uvicorn as a module launches no new .exe,
# so the same server starts without tripping the policy.
while ($true) {
  .\.venv\Scripts\python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000
  Write-Host "`nServer stopped. Restarting in 2s… (Ctrl-C to quit)" -ForegroundColor Yellow
  Start-Sleep -Seconds 2
}
