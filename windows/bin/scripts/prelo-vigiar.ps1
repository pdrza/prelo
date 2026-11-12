# prelo-vigiar: pasta monitorada: todo livro que chega em ENTRADA é convertido
# para SAIDA. Versão para Windows do bin/prelo-vigiar: mesmas opções e mensagens.
#
# Usa o FileSystemWatcher do Windows e, de reserva, confere a pasta a cada poucos
# segundos. Pode virar uma tarefa agendada que liga sozinha ao entrar no Windows
# (o equivalente ao serviço do systemd do usuário).
. "$PSScriptRoot\comum.ps1"
$Argumentos = @($args)

function ajuda {
    imprimir @'
Uso: prelo-vigiar [opções] ENTRADA SAIDA [-- opções do prelo convert]

Converte automaticamente os livros que aparecem na pasta ENTRADA.

  -f, --formato FMT       epub, azw3, mobi, cbz, pdf ou txt (padrão: azw3)
  -p, --perfil ID         dispositivo (padrão: automático)
  -i, --intervalo SEG     espera entre verificações de reserva (padrão: 5)
      --uma-vez           converte o que já está na pasta e sai
      --instalar-servico  cria e liga uma tarefa agendada com estas opções
                          (roda escondida e liga sozinha ao entrar no Windows)
      --remover-servico   desliga e remove a tarefa
  -h, --ajuda             mostra esta ajuda

Exemplo:
  prelo-vigiar -f azw3 -p kindle-pw5 --instalar-servico D:\Livros\Entrada D:\Livros\Kindle
'@
}

$tarefa = 'Prelo vigiar'

function valor([int]$i, [string]$falta) {
    if ($i + 1 -ge $Argumentos.Count) { morrer $falta }
    [string]$Argumentos[$i + 1]
}

$formato = 'azw3'; $perfil = 'auto'; $intervalo = '5'; $umaVez = $false; $instalar = $false
$pastas = [System.Collections.Generic.List[string]]::new()
$extras = [System.Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt $Argumentos.Count; $i++) {
    $a = [string]$Argumentos[$i]
    if ($a -in '-f', '--formato') { $formato = minusculas (valor $i 'falta o formato'); $i++ }
    elseif ($a -in '-p', '--perfil') { $perfil = valor $i 'falta o perfil'; $i++ }
    elseif ($a -in '-i', '--intervalo') { $intervalo = valor $i 'falta o intervalo'; $i++ }
    elseif ($a -eq '--uma-vez') { $umaVez = $true }
    elseif ($a -eq '--instalar-servico') { $instalar = $true }
    elseif ($a -eq '--remover-servico') {
        if (Get-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false
        }
        ok 'serviço removido'
        exit 0
    }
    elseif ($a -in '-h', '--ajuda', '--help') { ajuda; exit 0 }
    elseif ($a -eq '--') { for ($k = $i + 1; $k -lt $Argumentos.Count; $k++) { $extras.Add([string]$Argumentos[$k]) }; break }
    elseif ($a.StartsWith('-')) { morrer "opção desconhecida: $a (veja --ajuda)" }
    else { $pastas.Add($a) }
}
if ($pastas.Count -ne 2) { ajuda; exit 1 }
if (-not (formato_valido $formato)) { morrer "formato inválido: $formato" }
if ($intervalo -notmatch '^[1-9][0-9]*$') { morrer '--intervalo precisa ser um número de segundos' }
$entrada = caminho_absoluto $pastas[0]
$saida = caminho_absoluto $pastas[1]
if ([string]::Equals($entrada.TrimEnd('\'), $saida.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
    morrer 'ENTRADA e SAIDA precisam ser pastas diferentes'
}
[void][IO.Directory]::CreateDirectory($entrada)
[void][IO.Directory]::CreateDirectory($saida)
$rotulo = $formato.ToUpperInvariant()

# ---------------------------------------------------------------- serviço
if ($instalar) {
    $usuario = "$env:USERDOMAIN\$env:USERNAME"
    $comando = [System.Collections.Generic.List[string]]::new()
    $comando.AddRange([string[]]@('-NoLogo', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                                  '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
                                  '-f', $formato, '-p', $perfil, '-i', $intervalo, $entrada, $saida))
    if ($extras.Count) { $comando.Add('--'); $comando.AddRange($extras) }
    $linha = ($comando | ForEach-Object { argumento_windows $_ }) -join ' '
    $acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $linha
    $gatilho = New-ScheduledTaskTrigger -AtLogOn -User $usuario
    $ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $quem = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Settings $ajustes -Principal $quem `
        -Description "Prelo: converte os livros que chegam em $entrada" -Force | Out-Null
    Start-ScheduledTask -TaskName $tarefa
    ok "serviço ligado: livros em $entrada viram $rotulo em $saida"
    info "é a tarefa agendada `"$tarefa`"; liga sozinha ao entrar no Windows"
    info "acompanhar: os registros ficam em $PreloEstado (vigiar-*.log)"
    exit 0
}

# ---------------------------------------------------------------- vigia
try {
    $trava = [IO.FileStream]::new((Join-Path $saida '.prelo-vigiar.lock'), [IO.FileMode]::OpenOrCreate,
                                  [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch {
    if (erro_de_io $_) { morrer "já existe um prelo-vigiar gravando em $saida" }
    throw
}
if ($perfil -ne 'auto') { $extras.InsertRange(0, [string[]]@('--profile', $perfil)) }
$manifesto = Join-Path $saida '.prelo-lote.tsv'
$log = arquivo_de_log 'vigiar'
$script:LogSessao = $log  # rodando como tarefa agendada, não há terminal: as mensagens ficam no log

function tamanho([string]$f) { try { [IO.FileInfo]::new($f).Length } catch { -1 } }

function assinatura([string]$f) {  # "tamanho:data" (data em segundos, como o stat -c %Y)
    $info = [IO.FileInfo]::new($f)
    if (-not $info.Exists) { return '' }
    '{0}:{1}' -f $info.Length, ([DateTimeOffset]$info.LastWriteTimeUtc).ToUnixTimeSeconds()
}

function estavel([string]$f) {  # espera o arquivo parar de crescer (cópia ainda em andamento)
    $antes = tamanho $f
    Start-Sleep -Seconds 2
    $depois = tamanho $f
    ($antes -eq $depois) -and ($depois -gt 0)
}

$vistos = @{}  # arquivo → "tamanho:data" já tratado (evita reler a pasta toda)

function processar([string]$f) {
    if (-not (e_livro $f)) { return }
    if ((extensao $f) -eq $formato) { return }
    $sinal = assinatura $f
    if (-not $sinal -or ($vistos.ContainsKey($f) -and $vistos[$f] -eq $sinal)) { return }
    if (-not ((estavel $f) -or (estavel $f))) { return }
    $vistos[$f] = assinatura $f
    $hash = hash_arquivo $f
    if (manifesto_tem $manifesto $hash $formato $perfil) { return }
    $nome = [IO.Path]::GetFileName($f)
    info "convertendo $nome"
    $gerado = converter $f $formato $saida $log $extras
    if ($gerado -and [IO.File]::Exists($gerado)) {
        manifesto_anota $manifesto $hash $formato $perfil $gerado
        ok "$nome → $([IO.Path]::GetFileName($gerado))"
        notificar 'Prelo: livro convertido' ([IO.Path]::GetFileName($gerado))
    } else {
        erro "$nome (detalhes em $log)"
        notificar 'Prelo: falha na conversão' $nome
    }
}

function varrer {
    $arquivos = @([IO.Directory]::GetFiles($entrada) | Where-Object { -not [IO.Path]::GetFileName($_).StartsWith('.') })
    [Array]::Sort($arquivos, [StringComparer]::Ordinal)
    foreach ($f in $arquivos) { processar $f }
}

titulo "Vigiando $entrada → $saida ($rotulo)"
varrer
if ($umaVez) { exit 0 }

$segundos = [int]$intervalo
try {
    $vigia = [IO.FileSystemWatcher]::new($entrada)
    $vigia.IncludeSubdirectories = $false
    $vigia.NotifyFilter = [IO.NotifyFilters]'FileName, LastWrite, Size'
    foreach ($evento in 'Created', 'Changed', 'Renamed') {
        Register-ObjectEvent -InputObject $vigia -EventName $evento -SourceIdentifier "prelo.$evento" | Out-Null
    }
    $vigia.EnableRaisingEvents = $true
    info "usando o FileSystemWatcher do Windows (e conferindo a cada ${segundos}s)"
} catch {
    $vigia = $null
    info "FileSystemWatcher indisponível nesta pasta: verificando a cada ${segundos}s"
}
while ($true) {
    if ($vigia) {
        [void](Wait-Event -Timeout $segundos)
        Get-Event | Remove-Event  # a varredura olha a pasta toda
    } else {
        Start-Sleep -Seconds $segundos
    }
    varrer
}
