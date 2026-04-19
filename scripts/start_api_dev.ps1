$ErrorActionPreference = "Stop"
Set-Location "D:\Desktop\ResearchOS"
$env:PYTHONPATH = "D:\Desktop\ResearchOS"
& "D:\Desktop\ResearchOS\.venv\Scripts\python.exe" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010
