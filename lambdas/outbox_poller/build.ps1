# Outbox Poller Lambda 패키징
# Windows에서 Linux용 패키지 받으려면 --platform 옵션 필수

$ErrorActionPreference = "Stop"

$BuildDir = ".\build"
$ZipFile = ".\outbox_poller.zip"

# 이전 빌드 정리
if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}
if (Test-Path $ZipFile) {
    Remove-Item $ZipFile -Force
}

# 빌드 폴더 생성
New-Item -ItemType Directory -Path $BuildDir | Out-Null

# 의존성 설치 (Lambda Linux 환경 타겟)
Write-Host "Installing dependencies..."
pip install `
    --target $BuildDir `
    --platform manylinux2014_x86_64 `
    --python-version 3.12 `
    --only-binary=:all: `
    --upgrade `
    -r requirements.txt

# 핸들러 코드 복사
Write-Host "Copying handler..."
Copy-Item handler.py $BuildDir\
Copy-Item errors.py $BuildDir\
Copy-Item logger.py $BuildDir\

# zip 생성
Write-Host "Creating zip..."
Compress-Archive -Path "$BuildDir\*" -DestinationPath $ZipFile -Force

# 정리
Remove-Item $BuildDir -Recurse -Force

Write-Host "Done: $ZipFile"
Get-Item $ZipFile | Select-Object Name, Length