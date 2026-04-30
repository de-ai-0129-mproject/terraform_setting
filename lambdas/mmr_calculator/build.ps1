# MMR Calculator Lambda 패키징
$ErrorActionPreference = "Stop"

$BuildDir = ".\build"
$ZipFile = ".\mmr_calculator.zip"

# 이전 빌드 정리
if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}
if (Test-Path $ZipFile) {
    Remove-Item $ZipFile -Force
}

New-Item -ItemType Directory -Path $BuildDir | Out-Null

Write-Host "Installing dependencies..."
pip install `
    --target $BuildDir `
    --platform manylinux2014_x86_64 `
    --python-version 3.12 `
    --only-binary=:all: `
    --upgrade `
    -r requirements.txt

Write-Host "Copying handler files..."
Copy-Item handler.py $BuildDir\
Copy-Item mmr.py $BuildDir\
Copy-Item champions.py $BuildDir\
Copy-Item html_template.py $BuildDir\

Write-Host "Creating zip..."
Compress-Archive -Path "$BuildDir\*" -DestinationPath $ZipFile -Force

Remove-Item $BuildDir -Recurse -Force

Write-Host "Done: $ZipFile"
Get-Item $ZipFile | Select-Object Name, Length