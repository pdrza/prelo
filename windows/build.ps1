<#
.SYNOPSIS
    Gera o Prelo para Windows: o app (PyInstaller), o .zip portátil e o instalador (Inno Setup).

.DESCRIPTION
    Quem usa o resultado não precisa de Python. Quem gera precisa do Python 3.11+
    (python.org) e, para o instalador, do Inno Setup 6 (jrsoftware.org); sem o Inno
    Setup, saem só a pasta do app e o .zip portátil.

      dist\windows\Prelo\                         o app (prelo-app.exe, prelo.exe, utilitários)
      dist\Prelo-VERSÃO-windows-x64.zip            versão portátil: descompacte e abra o prelo-app.exe
      dist\Prelo-VERSÃO-windows-x64-setup.exe      instalador (menu Iniciar, "Abrir com", PATH)

.EXAMPLE
    .\windows\build.ps1
.EXAMPLE
    .\windows\build.ps1 -Python C:\Python313\python.exe -Testar
#>
param(
    [string]$Python,  # Python 3.11+ do build (padrão: py -3 ou o python do PATH)
    [string]$Iscc,    # compilador do Inno Setup (padrão: procura nos lugares de costume)
    [switch]$Testar,     # confere o resultado: linha de comando, uma conversão e a janela
    [switch]$SemJanela,  # com -Testar, pula o teste da janela (máquinas sem área de trabalho, como a CI)
    [switch]$Limpar      # recria do zero o ambiente de build
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }  # o prelo.exe responde em UTF-8

$Raiz = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Raiz 'build\windows'
$Dist = Join-Path $Raiz 'dist'
$App = Join-Path $Dist 'windows\Prelo'
$Venv = Join-Path $Build 'venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'

function passo([string]$t) { Write-Host "› $t" -ForegroundColor DarkGray }
function feito([string]$t) { Write-Host "✓ $t" -ForegroundColor Green }
function aviso([string]$t) { Write-Host "⚠ $t" -ForegroundColor Yellow }
function falhar([string]$t) { Write-Host "✗ $t" -ForegroundColor Red; exit 1 }
# No PowerShell 5.1, se quem chamou este script redirecionou a saída de erro (2>&1, um log,
# a CI), cada linha que um programa escreve nela vira um "erro" do PowerShell, e com
# ErrorActionPreference=Stop o build cairia no primeiro aviso do PyInstaller (que escreve
# o progresso ali). Os programas rodam com Continue e viram texto comum; quem decide se
# falhou é o código de saída.
function rodar([string]$exe, [string[]]$argumentos, [string]$erro) {
    $ErrorActionPreference = 'Continue'
    & $exe @argumentos 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { falhar "$erro (código $LASTEXITCODE)" }
}
# Só a saída padrão volta; a de erro aparece na tela como texto.
function capturar([string]$exe, [string[]]$argumentos) {
    $ErrorActionPreference = 'Continue'
    & $exe @argumentos 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Host "$_" } else { $_ }
    }
}
# Roda um programa só pela resposta (saída padrão), sem a saída de erro. No PowerShell 5.1,
# a saída de erro redirecionada de um programa vira erro fatal com ErrorActionPreference=Stop.
function perguntar([string]$exe, [string[]]$argumentos) {
    $ErrorActionPreference = 'Continue'
    $resposta = & $exe @argumentos 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    ($resposta | Out-String).Trim()
}

# ---------------------------------------------------------------- Python
function achar_python {
    if ($Python) { return $Python }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $exe = perguntar $py.Source @('-3', '-c', 'import sys; print(sys.executable)')
        if ($exe) { return $exe }
    }
    foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -notlike '*\WindowsApps\*') { return $c.Source }  # WindowsApps: atalho da Microsoft Store, não é o Python
    }
    falhar 'preciso do Python 3.11 ou mais novo (python.org); se ele não estiver no PATH, use -Python C:\caminho\python.exe'
}

Write-Host 'Prelo para Windows: build' -ForegroundColor White
$base = achar_python
$versaoPython = perguntar $base @('-c', 'import sys; print(sys.version.split()[0]) if sys.version_info >= (3, 11) else sys.exit(1)')
if (-not $versaoPython) { falhar "$base não é o Python 3.11 ou mais novo" }
feito "Python $versaoPython em $base"

if ($Limpar -and (Test-Path $Build)) { Remove-Item -Recurse -Force $Build }
if ((Test-Path $VenvPy) -and -not (perguntar $VenvPy @('-c', 'print(1)'))) {
    Remove-Item -Recurse -Force $Venv  # ambiente quebrado (o Python foi atualizado ou removido)
}
if (-not (Test-Path $VenvPy)) {
    passo "criando o ambiente de build em $Venv"
    rodar $base @('-m', 'venv', $Venv) 'não consegui criar o ambiente de build'
}
passo 'instalando o Prelo, as bibliotecas e o PyInstaller (pode levar alguns minutos)'
rodar $VenvPy @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip') 'falhou ao atualizar o pip'
rodar $VenvPy @('-m', 'pip', 'install', '--quiet', '--upgrade', "$Raiz[desktop]", 'pyinstaller>=6.10,<7') 'falhou ao instalar as dependências'
$Versao = perguntar $VenvPy @('-c', 'import prelo; print(prelo.__version__)')
if (-not $Versao) { falhar 'o Prelo foi instalado no ambiente de build, mas não importa' }
feito "Prelo $Versao"

# ---------------------------------------------------------------- app
passo 'gerando os executáveis (PyInstaller)'
rodar $VenvPy @('-m', 'PyInstaller', (Join-Path $PSScriptRoot 'prelo.spec'), '--noconfirm', '--clean', '--log-level', 'WARN',
                '--distpath', (Join-Path $Dist 'windows'), '--workpath', (Join-Path $Build 'pyinstaller')) 'o PyInstaller falhou'
foreach ($exe in 'prelo-app.exe', 'prelo.exe') {
    if (-not (Test-Path (Join-Path $App $exe))) { falhar "o PyInstaller não gerou o $exe" }
}
# utilitários ao lado do prelo.exe, como ficam no Prelo instalado
Copy-Item (Join-Path $PSScriptRoot 'bin\*.cmd') $App -Force
Copy-Item (Join-Path $PSScriptRoot 'bin\scripts') (Join-Path $App 'scripts') -Recurse -Force
Copy-Item (Join-Path $Raiz 'LICENSE') (Join-Path $App 'LICENSE.txt') -Force
$tamanho = (Get-ChildItem $App -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
feito ('app em {0} ({1:n0} MB)' -f $App, $tamanho)

# ---------------------------------------------------------------- testes
if ($Testar) {
    $prelo = Join-Path $App 'prelo.exe'
    $teste = Join-Path $Build 'teste'
    if (Test-Path $teste) { Remove-Item -Recurse -Force $teste }
    New-Item -ItemType Directory -Force $teste | Out-Null
    $env:PRELO_DATA_DIR = Join-Path $teste 'dados'  # não mexe na estante de quem está gerando

    passo 'testando a linha de comando'
    rodar $prelo @('--version') 'prelo.exe --version falhou'
    rodar $VenvPy @('-c', "import sys; sys.path.insert(0, r'$Raiz\tests'); import samples; samples.make_novel_pdf(__import__('pathlib').Path(r'$teste\romance.pdf'))") 'não consegui gerar o PDF de teste'
    $gerado = @(capturar $prelo @('convert', (Join-Path $teste 'romance.pdf'), '-f', 'azw3', '--mode', 'fixed', '-o', $teste, '--print-path')) | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or -not $gerado -or -not (Test-Path $gerado)) { falhar 'a conversão de teste falhou' }
    feito "conversão em layout fiel (renderização em paralelo): $(Split-Path -Leaf $gerado)"

    if (-not $SemJanela) {
        passo 'testando a janela (abre e fecha sozinha)'
        $relatorio = (capturar $prelo @('app', '--diagnostico')) -join "`n"
        if ($relatorio -notmatch '"resultado": "ok"') { Write-Host $relatorio; falhar 'a janela não carregou a interface' }
        feito 'janela nativa (WebView2) com a interface carregada'
    }
    Remove-Item Env:\PRELO_DATA_DIR
}

# ---------------------------------------------------------------- pacotes
$zip = Join-Path $Dist "Prelo-$Versao-windows-x64.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($App, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
feito ('portátil: {0} ({1:n0} MB)' -f $zip, ((Get-Item $zip).Length / 1MB))

function achar_iscc {
    if ($Iscc) { return $Iscc }
    $achado = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($achado) { return $achado.Source }
    $lugares = @()
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($base) { $lugares += Join-Path $base 'Inno Setup 6\ISCC.exe' }
    }
    foreach ($l in $lugares) { if (Test-Path $l) { return $l } }
    $null
}
$compilador = achar_iscc
if ($compilador) {
    passo 'gerando o instalador (Inno Setup)'
    rodar $compilador @('/Q', "/DVersao=$Versao", "/DOrigem=$App", "/DSaida=$Dist", (Join-Path $PSScriptRoot 'prelo.iss')) 'o Inno Setup falhou'
    $setup = Join-Path $Dist "Prelo-$Versao-windows-x64-setup.exe"
    feito ('instalador: {0} ({1:n0} MB)' -f $setup, ((Get-Item $setup).Length / 1MB))
} else {
    aviso 'Inno Setup 6 não encontrado: o instalador não foi gerado (https://jrsoftware.org/isinfo.php, ou use -Iscc)'
}
Write-Host 'Pronto.' -ForegroundColor White
