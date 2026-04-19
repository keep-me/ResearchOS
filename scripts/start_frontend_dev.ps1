$ErrorActionPreference = "Stop"
Set-Location "D:\Desktop\ResearchOS\frontend"
$env:VITE_PROXY_TARGET = "http://127.0.0.1:8010"
npx vite --host 127.0.0.1 --port 4317 --strictPort
