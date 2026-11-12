# Funções comuns aos utilitários do Prelo para Windows (prelo-lote, prelo-enviar, prelo-vigiar).
# É o lib/comum.sh dos utilitários do Linux, em PowerShell 5.1 (o que vem com o Windows).
# Uso: . "$PSScriptRoot\comum.ps1"
#
# Os .ps1 precisam ser gravados em UTF-8 *com* BOM: sem a marca, o Windows PowerShell 5.1
# lê os acentos como ANSI (conferido nos testes).

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # barras de progresso viram lixo na saída de erro redirecionada

$Utf8 = New-Object System.Text.UTF8Encoding $false
try { [Console]::OutputEncoding = $Utf8 } catch { }  # sem console (tarefa agendada): nada a ajustar

$PreloDados = if ($env:PRELO_DATA_DIR) { $env:PRELO_DATA_DIR }
              elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Prelo' }
              else { Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Prelo' }
$PreloEstado = Join-Path $PreloDados 'logs'
$PreloExtensoes = 'azw azw3 bmp cbz epub gif jpeg jpg kepub mobi pdb pdf png prc tif tiff webp zip'  # = formats.BOOK_EXTENSIONS (conferido nos testes)
$PreloFormatos = 'epub azw3 mobi cbz pdf txt'

$script:LogSessao = $null    # definido: as mensagens também vão para este arquivo (prelo-vigiar como serviço)
$script:PreloExe = $null
$script:Cores = $false
try { $script:Cores = -not [Console]::IsErrorRedirected } catch { }

# ------------------------------------------------------------------ mensagens
function data_iso { (Get-Date).ToString("yyyy-MM-dd'T'HH:mm:sszzz", [Globalization.CultureInfo]::InvariantCulture) }

function escrever([string]$Simbolo, [ConsoleColor]$Cor, [string]$Texto, [switch]$LinhaToda) {
    $linha = if ($Simbolo) { "$Simbolo $Texto" } else { $Texto }
    if ($script:Cores) {
        $antes = [Console]::ForegroundColor
        try {
            [Console]::ForegroundColor = $Cor
            if ($LinhaToda) { [Console]::Error.WriteLine($linha); return }
            [Console]::Error.Write($Simbolo)
        } finally {
            [Console]::ForegroundColor = $antes
        }
        [Console]::Error.WriteLine(" $Texto")
    } else {
        [Console]::Error.WriteLine($linha)
    }
    if ($script:LogSessao) { adicionar_linha $script:LogSessao ("{0} {1}" -f (data_iso), $linha) }
}

function info([string]$t)   { escrever '›' DarkGray $t }
function ok([string]$t)     { escrever '✓' Green $t }
function aviso([string]$t)  { escrever '⚠' Yellow $t }
function erro([string]$t)   { escrever '✗' Red $t }
function titulo([string]$t) { escrever '' White $t -LinhaToda }
function morrer([string]$t) { erro $t; exit 1 }
function imprimir([string]$t) { [Console]::Out.WriteLine($t) }  # saída padrão: ajuda, listas

# ------------------------------------------------------------------- arquivos
function caminho_absoluto([string]$p) {
    # relativo à pasta atual do PowerShell (que pode não ser a do processo)
    [IO.Path]::GetFullPath([IO.Path]::Combine($PWD.ProviderPath, $p))
}

function minusculas([string]$s) { $s.ToLowerInvariant() }

function extensao([string]$p) {
    $nome = [IO.Path]::GetFileName($p)
    $i = $nome.LastIndexOf('.')
    if ($i -lt 0) { return '' }
    $nome.Substring($i + 1).ToLowerInvariant()
}

# É um arquivo que o Prelo aceita como entrada?
function e_livro([string]$p) {
    $nome = [IO.Path]::GetFileName($p)
    if (-not $nome -or $nome.StartsWith('.') -or -not [IO.File]::Exists($p)) { return $false }
    $ext = extensao $p
    [bool]($ext -and (" $PreloExtensoes ").Contains(" $ext "))
}

function formato_valido([string]$f) { (" $PreloFormatos ").Contains(" $f ") }

# SHA-256 pelo .NET, como o sha256sum do Linux. O Get-FileHash vem de um módulo, e o Windows
# PowerShell aberto de dentro do PowerShell 7 herda a lista de módulos do 7 e não o encontra.
function hash_arquivo([string]$p) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $f = [IO.File]::OpenRead((caminho_absoluto $p))
    try { -join ($sha.ComputeHash($f) | ForEach-Object { $_.ToString('x2') }) }
    finally { $f.Dispose(); $sha.Dispose() }
}

function erro_de_io($registro) { $registro.Exception.GetBaseException() -is [IO.IOException] }

# Acrescenta uma linha terminada em LF (os registros são os mesmos do Linux). Enquanto
# grava, ninguém mais escreve: é a trava (flock) do comum.sh.
function adicionar_linha([string]$arquivo, [string]$linha) {
    adicionar_texto $arquivo ($linha + "`n")
}

function adicionar_texto([string]$arquivo, [string]$texto) {
    $pasta = [IO.Path]::GetDirectoryName($arquivo)
    if ($pasta) { [void][IO.Directory]::CreateDirectory($pasta) }
    $bytes = $Utf8.GetBytes($texto)
    for ($tentativa = 1; ; $tentativa++) {
        try {
            $fs = [IO.FileStream]::new($arquivo, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
            try { $fs.Write($bytes, 0, $bytes.Length) } finally { $fs.Dispose() }
            return
        } catch {
            if ($tentativa -ge 50 -or -not (erro_de_io $_)) { throw }
            Start-Sleep -Milliseconds 100  # outro processo está gravando
        }
    }
}

function ler_linhas([string]$arquivo) {
    if (-not [IO.File]::Exists($arquivo)) { return }
    $fs = [IO.FileStream]::new($arquivo, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    $leitor = [IO.StreamReader]::new($fs, $Utf8)
    try { $texto = $leitor.ReadToEnd() } finally { $leitor.Dispose() }
    $texto -split "`r?`n" | Where-Object { $_ }
}

# Manifesto: uma linha por conversão feita (hash do original, formato, perfil,
# arquivo gerado). Reconhece o mesmo livro mesmo renomeado ou movido.
function manifesto_tem([string]$manifesto, [string]$hash, [string]$formato, [string]$perfil) {
    $prefixo = "$hash`t$formato`t$perfil`t"
    foreach ($linha in @(ler_linhas $manifesto)) {
        if ($linha.StartsWith($prefixo, [StringComparison]::Ordinal)) { return $true }
    }
    $false
}

function manifesto_anota([string]$manifesto, [string]$hash, [string]$formato, [string]$perfil, [string]$saida) {
    adicionar_linha $manifesto ("{0}`t{1}`t{2}`t{3}`t{4}" -f $hash, $formato, $perfil, $saida, (data_iso))
}

function arquivo_de_log([string]$prefixo) {
    [void][IO.Directory]::CreateDirectory($PreloEstado)
    Join-Path $PreloEstado ('{0}-{1}.log' -f $prefixo, (Get-Date -Format 'yyyyMMdd-HHmmss'))
}

# ------------------------------------------------------------------ o motor
# Caminho do prelo.exe (motor): variável PRELO_BIN, Prelo instalado (esta pasta fica
# dentro dele), ambiente .venv do repositório (desenvolvimento) ou PATH.
function prelo_cmd {
    if ($script:PreloExe) { return $script:PreloExe }
    if ($env:PRELO_BIN) { $script:PreloExe = $env:PRELO_BIN; return $script:PreloExe }
    $candidatos = @((Join-Path $PSScriptRoot '..\prelo.exe'),
                    (Join-Path $PSScriptRoot '..\..\..\.venv\Scripts\prelo.exe'))
    if ($env:LOCALAPPDATA) { $candidatos += Join-Path $env:LOCALAPPDATA 'Programs\Prelo\prelo.exe' }
    foreach ($c in $candidatos) {
        if ([IO.File]::Exists($c)) { $script:PreloExe = [IO.Path]::GetFullPath($c); return $script:PreloExe }
    }
    $achado = Get-Command prelo.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($achado) { $script:PreloExe = $achado.Source; return $script:PreloExe }
    morrer "comando 'prelo' não encontrado. Instale o Prelo para Windows ou defina PRELO_BIN"
}

# Um argumento na linha de comando do Windows, com as aspas e barras que o prelo.exe
# (e qualquer programa em C ou Python) desfaz exatamente.
function argumento_windows([string]$a) {
    if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
    $barra = [char]92
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $barras = 0
    foreach ($c in $a.ToCharArray()) {
        if ($c -eq $barra) { $barras++; continue }
        if ($c -eq [char]'"') { [void]$sb.Append($barra, 2 * $barras + 1).Append('"'); $barras = 0; continue }
        [void]$sb.Append($barra, $barras).Append($c)
        $barras = 0
    }
    [void]$sb.Append($barra, 2 * $barras).Append('"')
    $sb.ToString()
}

# Inicia o prelo.exe sem janela, lendo as saídas em UTF-8 (o --print-path escreve em UTF-8).
function iniciar_prelo([string[]]$Argumentos) {
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = prelo_cmd
    $psi.Arguments = (@($Argumentos) | ForEach-Object { argumento_windows $_ }) -join ' '
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $Utf8
    $psi.StandardErrorEncoding = $Utf8
    $p = [Diagnostics.Process]::Start($psi)
    [pscustomobject]@{ Processo = $p; Saida = $p.StandardOutput.ReadToEndAsync(); Erros = $p.StandardError.ReadToEndAsync() }
}

# Espera o prelo.exe terminar; a saída de erro vai para o log. Devolve o caminho
# gerado (última linha do --print-path) ou $null se falhou.
function terminar_prelo($execucao, [string]$log) {
    $execucao.Processo.WaitForExit()
    $saida = $execucao.Saida.Result
    $erros = $execucao.Erros.Result
    $codigo = $execucao.Processo.ExitCode
    $execucao.Processo.Dispose()
    if ($erros) { adicionar_texto $log $erros }
    if ($codigo -ne 0) { return $null }
    $linhas = @($saida -split "`r?`n" | Where-Object { $_ })
    if ($linhas.Count) { return $linhas[-1] }
    $null
}

function argumentos_de_conversao([string]$origem, [string]$formato, [string]$pasta, $extras) {
    $lista = [System.Collections.Generic.List[string]]::new()
    $lista.AddRange([string[]]@('convert', $origem, '-f', $formato, '-o', $pasta, '--print-path'))
    if ($null -ne $extras) { foreach ($e in $extras) { $lista.Add([string]$e) } }
    , $lista.ToArray()
}

# Converte um arquivo; devolve o caminho gerado. O log da conversão vai para $log.
# converter ORIGEM FORMATO PASTA_SAIDA LOG [opções extras do prelo convert]
function converter([string]$origem, [string]$formato, [string]$pasta, [string]$log, $extras) {
    [void][IO.Directory]::CreateDirectory($pasta)
    terminar_prelo (iniciar_prelo (argumentos_de_conversao $origem $formato $pasta $extras)) $log
}

# ---------------------------------------------------------------- notificação
# O atalho do instalador registra o Prelo para notificações (Prelo.Prelo); sem ele,
# elas saem em nome do Windows PowerShell.
function id_de_notificacao {
    $menu = [Environment]::GetFolderPath('Programs')
    foreach ($atalho in @((Join-Path $menu 'Prelo.lnk'), (Join-Path $menu 'Prelo\Prelo.lnk'))) {
        if ([IO.File]::Exists($atalho)) { return 'Prelo.Prelo' }
    }
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
}

function notificar([string]$titulo, [string]$texto) {
    if ($env:PRELO_NOTIFICAR -eq '0') { return }
    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $t = [Security.SecurityElement]::Escape($titulo)
        $c = [Security.SecurityElement]::Escape($texto)
        $xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
        $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$t</text><text>$c</text></binding></visual></toast>")
        $aviso = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier((id_de_notificacao)).Show($aviso)
    } catch { }  # a notificação é cortesia: o resumo continua no terminal e no log
}
