# prelo-lote: converte muitos livros de uma vez (arquivos e pastas inteiras).
# Versão para Windows do bin/prelo-lote: mesmas opções, mensagens e manifesto.
#
# Lembra o que já converteu (pelo conteúdo do arquivo, não pelo nome): rodar de
# novo na mesma pasta só converte os livros novos.
. "$PSScriptRoot\comum.ps1"
$Argumentos = @($args)

function ajuda {
    imprimir @'
Uso: prelo-lote [opções] ARQUIVO|PASTA... [-- opções do prelo convert]

Converte livros (PDF, EPUB, AZW3/MOBI, CBZ, imagens) em lote.

  -f, --formato FMT     epub, azw3, mobi, cbz, pdf ou txt (padrão: azw3)
  -p, --perfil ID       dispositivo, veja "prelo profiles" (padrão: automático)
  -o, --saida PASTA     onde gravar (padrão: ao lado de cada original)
  -r, --recursivo       inclui subpastas
  -j, --paralelo N      conversões ao mesmo tempo (padrão: 1; cada uma já usa
                        todos os núcleos para renderizar páginas)
      --refazer         converte de novo o que já foi convertido
      --mesmo-formato   inclui arquivos que já estão no formato pedido
  -n, --simular         só mostra o que seria convertido
  -h, --ajuda           mostra esta ajuda

Exemplos:
  prelo-lote -f azw3 -p kindle-pw5 D:\Livros
  prelo-lote -r -f epub -o D:\Convertidos D:\Downloads -- --mode fixed --temperature 4800
'@
}

function valor([int]$i, [string]$falta) {
    if ($i + 1 -ge $Argumentos.Count) { morrer $falta }
    [string]$Argumentos[$i + 1]
}

$formato = 'azw3'; $perfil = 'auto'; $saida = ''; $recursivo = $false; $paralelo = '1'
$refazer = $false; $mesmo = $false; $simular = $false
$entradas = [System.Collections.Generic.List[string]]::new()
$extras = [System.Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt $Argumentos.Count; $i++) {
    $a = [string]$Argumentos[$i]
    if ($a -in '-f', '--formato') { $formato = minusculas (valor $i 'falta o formato'); $i++ }
    elseif ($a -in '-p', '--perfil') { $perfil = valor $i 'falta o perfil'; $i++ }
    elseif ($a -in '-o', '--saida') { $saida = valor $i 'falta a pasta'; $i++ }
    elseif ($a -in '-r', '--recursivo') { $recursivo = $true }
    elseif ($a -in '-j', '--paralelo') { $paralelo = valor $i 'falta o número'; $i++ }
    elseif ($a -eq '--refazer') { $refazer = $true }
    elseif ($a -eq '--mesmo-formato') { $mesmo = $true }
    elseif ($a -in '-n', '--simular') { $simular = $true }
    elseif ($a -in '-h', '--ajuda', '--help') { ajuda; exit 0 }
    elseif ($a -eq '--') { for ($k = $i + 1; $k -lt $Argumentos.Count; $k++) { $extras.Add([string]$Argumentos[$k]) }; break }
    elseif ($a.StartsWith('-')) { morrer "opção desconhecida: $a (veja --ajuda)" }
    else { $entradas.Add($a) }
}

if ($entradas.Count -eq 0) { ajuda; exit 1 }
if (-not (formato_valido $formato)) { morrer "formato inválido: $formato (use: $PreloFormatos)" }
if ($paralelo -notmatch '^[1-9][0-9]*$') { morrer '--paralelo precisa ser um número maior que zero' }
$paralelo = [int]$paralelo
if ($perfil -ne 'auto') { $extras.InsertRange(0, [string[]]@('--profile', $perfil)) }
if ($saida) { $saida = caminho_absoluto $saida }
$rotulo = $formato.ToUpperInvariant()

# ---------------------------------------------------------------- arquivos
# Livros de uma pasta, em ordem; sem arquivos e pastas ocultos (".algo"), sem
# entrar em atalhos de pasta (junções) e sem descer na pasta de saída.
function livros_na_pasta([string]$pasta, [bool]$subpastas, [string]$pular) {
    $achados = [System.Collections.Generic.List[string]]::new()
    $pendentes = [System.Collections.Generic.Stack[string]]::new()
    $pendentes.Push($pasta)
    while ($pendentes.Count) {
        $atual = $pendentes.Pop()
        try {
            $itens = @([IO.DirectoryInfo]::new($atual).GetFileSystemInfos())
        } catch {
            aviso "sem permissão para ler a pasta: $atual"
            continue
        }
        foreach ($item in $itens) {
            if ($item.Name.StartsWith('.')) { continue }
            if ($item -is [IO.DirectoryInfo]) {
                $atalho = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
                # "-o D:\Kindle\" chega com a barra no fim; o FullName de uma pasta vem sem ela
                if ($subpastas -and -not $atalho -and -not ($pular -and [string]::Equals($item.FullName, $pular.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase))) {
                    $pendentes.Push($item.FullName)
                }
            } elseif (e_livro $item.FullName) {
                $achados.Add($item.FullName)
            }
        }
    }
    $lista = $achados.ToArray()
    [Array]::Sort($lista, [StringComparer]::Ordinal)
    $lista
}

$arquivos = [System.Collections.Generic.List[string]]::new()
$script:puladosFormato = 0
function adicionar([string]$f) {
    if (-not $mesmo -and (extensao $f) -eq $formato) { $script:puladosFormato++; return }
    $arquivos.Add($f)
}
foreach ($e in $entradas) {
    $absoluto = caminho_absoluto $e
    if ([IO.Directory]::Exists($absoluto)) {
        foreach ($f in @(livros_na_pasta $absoluto $recursivo $saida)) { adicionar $f }
    } elseif (e_livro $absoluto) {
        adicionar $absoluto
    } else {
        aviso "ignorado (não é um livro aceito): $e"
    }
}

if ($arquivos.Count -eq 0) {
    if ($script:puladosFormato) { morrer "nenhum livro para converter ($($script:puladosFormato) já em $rotulo; use --mesmo-formato)" }
    morrer 'nenhum livro para converter'
}

if ($simular) {
    titulo "Seriam convertidos para ${rotulo}:"
    foreach ($f in $arquivos) { imprimir "  $f" }
    exit 0
}

# ---------------------------------------------------------------- conversão
$log = arquivo_de_log 'lote'
titulo "Convertendo $($arquivos.Count) livro(s) para $rotulo"
$script:nOk = 0; $script:nPulados = 0; $script:nErros = 0
$rodando = [System.Collections.ArrayList]::new()

function concluir($item) {
    $gerado = terminar_prelo $item.Execucao $log
    $nome = [IO.Path]::GetFileName($item.Origem)
    if ($gerado -and [IO.File]::Exists($gerado)) {
        manifesto_anota $item.Manifesto $item.Hash $formato $perfil $gerado
        $script:nOk++
        ok "$nome → $([IO.Path]::GetFileName($gerado))"
    } else {
        $script:nErros++
        erro "$nome (detalhes em $log)"
    }
}

# Espera até haver menos de $limite conversões rodando (1 = esperar todas).
function esperar_vaga([int]$limite) {
    while ($rodando.Count -ge $limite) {
        $terminou = $false
        for ($k = $rodando.Count - 1; $k -ge 0; $k--) {
            $item = $rodando[$k]
            if ($item.Execucao.Processo.HasExited) {
                $rodando.RemoveAt($k)
                concluir $item
                $terminou = $true
            }
        }
        if (-not $terminou) { Start-Sleep -Milliseconds 150 }
    }
}

foreach ($origem in $arquivos) {
    $pasta = if ($saida) { $saida } else { [IO.Path]::GetDirectoryName($origem) }
    $manifesto = Join-Path $pasta '.prelo-lote.tsv'
    $hash = hash_arquivo $origem
    if (-not $refazer -and (manifesto_tem $manifesto $hash $formato $perfil)) {
        $script:nPulados++
        info "já convertido: $([IO.Path]::GetFileName($origem))"
        continue
    }
    esperar_vaga $paralelo
    [void][IO.Directory]::CreateDirectory($pasta)
    $execucao = iniciar_prelo (argumentos_de_conversao $origem $formato $pasta $extras)
    [void]$rodando.Add([pscustomobject]@{ Origem = $origem; Hash = $hash; Manifesto = $manifesto; Execucao = $execucao })
}
esperar_vaga 1

[Console]::Error.WriteLine()
titulo "Resumo: $($script:nOk) convertido(s), $($script:nPulados) já convertido(s), $($script:nErros) com erro"
if ($script:puladosFormato) { info "$($script:puladosFormato) arquivo(s) já em $rotulo ficaram de fora (use --mesmo-formato)" }
if ($script:nErros) { info "log: $log" }
notificar 'Prelo: lote concluído' "$($script:nOk) convertido(s), $($script:nErros) com erro"
if ($script:nErros) { exit 1 }
exit 0
