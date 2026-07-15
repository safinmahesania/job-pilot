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

.\.venv\Scripts\uvicorn.exe src.api:app --host 127.0.0.1 --port 8000
