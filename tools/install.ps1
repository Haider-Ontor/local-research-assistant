# Installs what Research Assistant needs to run, inside its own folder: Python (via uv) and the Python libraries.
# Run by "Start Research Assistant.bat"; quick when everything is already installed. The AI engine and the AI models
# are downloaded by the app itself (its first-run setup), so it can suggest ones that suit this computer.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # makes Invoke-WebRequest much faster
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$app = Split-Path -Parent $PSScriptRoot
Set-Location $app
$req = Join-Path $app "requirements.txt"
$py = Join-Path $app ".venv\Scripts\python.exe"
$stamp = Join-Path $app ".venv\installed-requirements.sha256"
$want = (Get-FileHash $req -Algorithm SHA256).Hash
if ((Test-Path $py) -and (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $want)) { exit 0 }
$fresh = -not (Test-Path $py)

if ($fresh) {
    Write-Host ""
    Write-Host "First start: installing Python and the app's libraries into this folder (about 400 MB, a few minutes)."
    Write-Host "This happens only once."
} else {
    Write-Host "Updating the app's Python libraries..."
}

# uv: a small, fast installer that brings its own Python, so nothing needs to be installed on the computer itself
$catalog = Get-Content (Join-Path $app "catalog.json") -Raw | ConvertFrom-Json
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") { "arm64" } else { "x64" }
$uvInfo = $catalog.uv.$arch
$uvDir = Join-Path $app "tools\uv"
$uv = Join-Path $uvDir "uv.exe"
if (-not (Test-Path $uv)) {
    Write-Host "Downloading uv $($catalog.uv.version)..."
    New-Item -ItemType Directory -Force $uvDir | Out-Null
    $zip = Join-Path $env:TEMP $uvInfo.name
    Invoke-WebRequest $uvInfo.url -OutFile $zip -UseBasicParsing
    if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $uvInfo.sha256.ToUpper()) {
        Remove-Item $zip
        throw "The uv download is damaged (its checksum doesn't match). Check the internet connection and try again."
    }
    Expand-Archive $zip -DestinationPath $uvDir -Force
    Remove-Item $zip
    $found = Get-ChildItem $uvDir -Recurse -Filter uv.exe | Select-Object -First 1
    if (-not $found) { throw "uv.exe wasn't in the uv download." }
    if ($found.FullName -ne $uv) { Copy-Item $found.FullName $uv }
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $app "tools\python"       # Python lives inside the app folder
$env:UV_CACHE_DIR = Join-Path $app "tools\uv-cache"               # ... and so does uv's working folder
$env:UV_PYTHON_PREFERENCE = "only-managed"
if ($fresh) {
    Write-Host "Installing Python 3.13..."
    & $uv venv --python 3.13 (Join-Path $app ".venv")
    if ($LASTEXITCODE) { throw "Couldn't set up Python (uv venv failed)." }
}
Write-Host "Installing the libraries (Streamlit, PyMuPDF, ...)..."
& $uv pip install --no-cache --python $py -r $req
if ($LASTEXITCODE) { throw "Couldn't install the libraries (see the messages above). Check the internet connection and try again." }
Set-Content $stamp $want

if ($fresh -and -not $env:RA_NO_SHORTCUT) {           # a desktop shortcut, so next time it's one double-click
    try {
        $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Research Assistant.lnk"
        if (-not (Test-Path $lnk)) {
            $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
            $s.TargetPath = Join-Path $app "Start Research Assistant.bat"
            $s.WorkingDirectory = $app
            $s.IconLocation = Join-Path $app "icon.ico"
            $s.Description = "Chat with your research papers"
            $s.Save()
            Write-Host "Added a 'Research Assistant' shortcut to your desktop."
        }
    } catch { Write-Host "(Couldn't add a desktop shortcut: $_)" }
}
Write-Host "Done."
