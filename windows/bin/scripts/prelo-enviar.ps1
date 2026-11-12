# prelo-enviar: manda livros para o leitor ligado no USB (Kindle ou Kobo).
# Versão para Windows do bin/prelo-enviar: mesmas opções, mensagens e registro.
#
# Detecta o aparelho (uma unidade removível, como E:\), escolhe o formato que ele
# lê (AZW3 no Kindle, EPUB no Kobo), converte só o que precisa, copia para a pasta
# certa e, se pedido, ejeta. É o modo "ao enviar para o dispositivo" do Calibre.
. "$PSScriptRoot\comum.ps1"
$Argumentos = @($args)

function ajuda {
    imprimir @'
Uso: prelo-enviar [opções] ARQUIVO... [-- opções do prelo convert]

Converte e copia livros para o Kindle ou Kobo conectado pelo USB.

  -d, --dispositivo PASTA   unidade do leitor, como E:\ (padrão: detecta)
  -p, --perfil ID           tela do aparelho (padrão: kindle-pw5 / kobo-libra)
  -m, --modo MODO           auto, fixed (layout fiel) ou reflow (padrão: auto)
      --converter-sempre    converte até o que o leitor já abre (EPUB no Kobo...)
  -e, --ejetar              ejeta o leitor no fim, pronto para desconectar
  -l, --listar              só lista os leitores conectados
  -h, --ajuda               mostra esta ajuda

Exemplos:
  prelo-enviar livro.pdf
  prelo-enviar -p kindle-scribe -m fixed -e revista.pdf manga.cbz
'@
}

# Pastas onde podem estar leitores: a raiz de cada unidade removível (E:\, F:\...).
# PRELO_MONTAGENS substitui: pastas separadas por ";", cujas subpastas são os leitores.
function montagens {
    if ($env:PRELO_MONTAGENS) {
        foreach ($d in $env:PRELO_MONTAGENS.Split(';')) {
            if ($d -and [IO.Directory]::Exists($d)) {
                foreach ($sub in [IO.Directory]::GetDirectories($d)) { $sub }
            }
        }
        return
    }
    foreach ($u in [IO.DriveInfo]::GetDrives()) {
        if ($u.DriveType -eq [IO.DriveType]::Removable -and $u.IsReady) { $u.RootDirectory.FullName }
    }
}

# "kindle", "kobo" ou nada.
function marca_de([string]$m) {
    if ([IO.Directory]::Exists((Join-Path $m 'documents')) -and [IO.Directory]::Exists((Join-Path $m 'system'))) { return 'kindle' }
    if ([IO.Directory]::Exists((Join-Path $m '.kobo'))) { return 'kobo' }
    ''
}

function livre_em_kb([string]$pasta) {
    try { [long]([IO.DriveInfo]::new([IO.Path]::GetPathRoot($pasta)).AvailableFreeSpace / 1024) } catch { [long]::MaxValue }
}

function tamanho_legivel([long]$kb) {  # como o df -h: 980M, 1.2G, 15G
    $valor = [double]$kb
    foreach ($unidade in 'K', 'M', 'G', 'T') {
        if ($valor -lt 1024 -or $unidade -eq 'T') {
            $texto = if ($valor -lt 10 -and $unidade -ne 'K') { $valor.ToString('0.0', [Globalization.CultureInfo]::InvariantCulture) } else { [Math]::Round($valor).ToString() }
            return "$texto$unidade"
        }
        $valor /= 1024
    }
}

function listar {
    $achou = $false
    foreach ($m in @(montagens)) {
        $marca = marca_de $m
        if (-not $marca) { continue }
        imprimir ('{0,-8} {1}  ({2} livres)' -f $marca, $m, (tamanho_legivel (livre_em_kb $m)))
        $achou = $true
    }
    $achou
}

function valor([int]$i, [string]$falta) {
    if ($i + 1 -ge $Argumentos.Count) { morrer $falta }
    [string]$Argumentos[$i + 1]
}

$dispositivo = ''; $perfil = ''; $modo = 'auto'; $sempre = $false; $ejetar = $false
$arquivos = [System.Collections.Generic.List[string]]::new()
$extras = [System.Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt $Argumentos.Count; $i++) {
    $a = [string]$Argumentos[$i]
    if ($a -in '-d', '--dispositivo') { $dispositivo = valor $i 'falta a pasta'; $i++ }
    elseif ($a -in '-p', '--perfil') { $perfil = valor $i 'falta o perfil'; $i++ }
    elseif ($a -in '-m', '--modo') { $modo = valor $i 'falta o modo'; $i++ }
    elseif ($a -eq '--converter-sempre') { $sempre = $true }
    elseif ($a -in '-e', '--ejetar') { $ejetar = $true }
    elseif ($a -in '-l', '--listar') { if (-not (listar)) { morrer 'nenhum Kindle ou Kobo conectado' }; exit 0 }
    elseif ($a -in '-h', '--ajuda', '--help') { ajuda; exit 0 }
    elseif ($a -eq '--') { for ($k = $i + 1; $k -lt $Argumentos.Count; $k++) { $extras.Add([string]$Argumentos[$k]) }; break }
    elseif ($a.StartsWith('-')) { morrer "opção desconhecida: $a (veja --ajuda)" }
    else { $arquivos.Add($a) }
}
if ($arquivos.Count -eq 0) { ajuda; exit 1 }
if ($modo -notin 'auto', 'fixed', 'reflow') { morrer "modo inválido: $modo" }

# ---------------------------------------------------------------- aparelho
if (-not $dispositivo) {
    $achados = @(montagens | Where-Object { marca_de $_ })
    if ($achados.Count -eq 0) { morrer 'nenhum Kindle ou Kobo conectado (ligue pelo USB ou use --dispositivo PASTA)' }
    if ($achados.Count -gt 1) { morrer "mais de um leitor conectado: escolha com --dispositivo ($($achados -join ' '))" }
    $dispositivo = $achados[0]
}
$dispositivo = caminho_absoluto $dispositivo
if (-not [IO.Directory]::Exists($dispositivo)) { morrer "pasta do leitor não existe: $dispositivo" }
$marca = marca_de $dispositivo
switch ($marca) {
    'kindle' { $formato = 'azw3'; $destino = Join-Path $dispositivo 'documents'; $aceitos = 'azw3 mobi'; if (-not $perfil) { $perfil = 'kindle-pw5' } }
    'kobo' { $formato = 'epub'; $destino = $dispositivo; $aceitos = 'epub kepub cbz'; if (-not $perfil) { $perfil = 'kobo-libra' } }
    default { morrer "não reconheci o leitor em $dispositivo (esperava Kindle ou Kobo)" }
}
$nomeDaMarca = $marca.Substring(0, 1).ToUpperInvariant() + $marca.Substring(1)
titulo "Enviando para $nomeDaMarca em $dispositivo"

# ---------------------------------------------------------------- envio
$log = arquivo_de_log 'enviar'
$temporaria = Join-Path ([IO.Path]::GetTempPath()) ('prelo-enviar-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
[void][IO.Directory]::CreateDirectory($temporaria)
$script:enviados = 0; $script:falhas = 0; $script:ultimoAlvo = ''

function mesmo_conteudo([string]$a, [string]$b) {
    if ([IO.FileInfo]::new($a).Length -ne [IO.FileInfo]::new($b).Length) { return $false }
    (hash_arquivo $a) -eq (hash_arquivo $b)
}

function copiar([string]$arquivo) {
    $nome = [IO.Path]::GetFileName($arquivo)
    $alvo = Join-Path $destino $nome
    if (-not [IO.File]::Exists($arquivo)) {
        erro "arquivo a enviar não encontrado: $arquivo"
        return $false
    }
    if ([IO.File]::Exists($alvo) -and (mesmo_conteudo $arquivo $alvo)) {
        info "já está no leitor: $nome"
        return $true
    }
    $tamanho = [long][Math]::Ceiling([IO.FileInfo]::new($arquivo).Length / 1024)
    if ($tamanho + 1024 -gt (livre_em_kb $dispositivo)) {
        erro "sem espaço no leitor para $nome"
        return $false
    }
    if ([IO.File]::Exists($alvo)) {  # nunca sobrescreve: mesmo nome, conteúdo diferente
        $base = [IO.Path]::GetFileNameWithoutExtension($nome)
        $ext = [IO.Path]::GetExtension($nome)
        $n = 2
        while ([IO.File]::Exists((Join-Path $destino "$base ($n)$ext"))) { $n++ }
        $alvo = Join-Path $destino "$base ($n)$ext"
    }
    $parcial = "$alvo.prelo-parcial"
    try {
        [IO.File]::Copy($arquivo, $parcial, $false)
        [IO.File]::Move($parcial, $alvo)
    } catch {
        try { if ([IO.File]::Exists($parcial)) { [IO.File]::Delete($parcial) } } catch { }
        erro "não foi possível copiar $nome para o leitor"
        return $false
    }
    $script:ultimoAlvo = $alvo
    ok ([IO.Path]::GetFileName($alvo))
    $true
}

# Registro do que já foi enviado, na raiz do leitor (o mesmo do Linux): pelo hash do
# original, para reconhecer o livro mesmo convertido de novo (cada AZW3 tem um id novo).
$registro = Join-Path $dispositivo '.prelo-enviados.tsv'
function ja_enviado([string]$hash) {
    $nome = ''
    foreach ($linha in @(ler_linhas $registro)) {
        $c = $linha.Split("`t")
        if ($c.Count -ge 4 -and $c[0] -eq $hash -and $c[1] -eq $formato -and $c[2] -eq $perfil) { $nome = $c[3] }
    }
    $nome -and [IO.File]::Exists((Join-Path $destino $nome))
}

function ejetar_leitor([string]$pasta) {  # só a raiz de uma unidade (E:\) pode ser ejetada
    $raiz = [IO.Path]::GetPathRoot($pasta)
    if (-not $raiz -or $raiz.TrimEnd('\') -ne $pasta.TrimEnd('\')) { return $false }
    $unidade = $raiz.TrimEnd('\')
    try {
        $item = (New-Object -ComObject Shell.Application).Namespace(17).ParseName($unidade)  # 17 = Este Computador
        if (-not $item) { return $false }
        $item.InvokeVerb('Eject')
    } catch {
        return $false
    }
    for ($k = 0; $k -lt 40; $k++) {  # o Windows ejeta em segundo plano: espera a unidade sumir
        Start-Sleep -Milliseconds 250
        if (-not [IO.DriveInfo]::new($unidade).IsReady) { return $true }
    }
    $false
}

try {
    foreach ($f in $arquivos) {
        $absoluto = caminho_absoluto $f
        if (-not (e_livro $absoluto)) {
            erro "não é um livro aceito: $f"
            $script:falhas++
            continue
        }
        $ext = extensao $absoluto
        $hash = hash_arquivo $absoluto
        if (ja_enviado $hash) {
            info "já está no leitor: $([IO.Path]::GetFileName($absoluto))"
            continue
        }
        if (-not $sempre -and (" $aceitos ").Contains(" $ext ")) {
            $pronto = $absoluto  # o leitor já abre este formato
        } else {
            $extrasConversao = @('--profile', $perfil, '--mode', $modo) + $extras.ToArray()
            $pronto = converter $absoluto $formato $temporaria $log $extrasConversao
            if (-not $pronto -or -not [IO.File]::Exists($pronto)) {
                erro "falhou ao converter $([IO.Path]::GetFileName($absoluto)) (detalhes em $log)"
                $script:falhas++
                continue
            }
        }
        $script:ultimoAlvo = ''
        if (copiar $pronto) {
            $script:enviados++
            # sem arquivo de trava: no leitor fica só o registro (um envio por vez)
            if ($script:ultimoAlvo) {
                adicionar_linha $registro ("{0}`t{1}`t{2}`t{3}`t{4}" -f $hash, $formato, $perfil, [IO.Path]::GetFileName($script:ultimoAlvo), (data_iso))
            }
        } else {
            $script:falhas++
        }
    }
} finally {
    Remove-Item -LiteralPath $temporaria -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Error.WriteLine()
titulo "Resumo: $($script:enviados) enviado(s), $($script:falhas) com erro"
if ($ejetar) {
    if (ejetar_leitor $dispositivo) { ok 'leitor ejetado: pode desconectar' }
    else { aviso 'não consegui ejetar; use "Ejetar" no Explorador de Arquivos' }
}
notificar "Prelo: enviado para o $nomeDaMarca" "$($script:enviados) livro(s), $($script:falhas) com erro"
if ($script:falhas) { exit 1 }
exit 0
