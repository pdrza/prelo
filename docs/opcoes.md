<!-- Arquivo gerado por tools/gerar_docs.py a partir do código. Não edite à mão. -->

# Opções de conversão

O Prelo tem 35 opções. Cada uma tem o mesmo nome na interface, na linha de comando (`--nome-da-opcao`), na API (`"options": {"nome_da_opcao": valor}`) e nas configurações salvas. Nome desconhecido é **erro**, com sugestão do nome mais parecido; valor fora da faixa também.

Como uma opção chega ao valor final (níveis, travas e de onde veio cada valor) está em [conversao.md](conversao.md#prioridade-das-opções).

## Conversão

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Modo de conversão**<br>`mode` | `--mode CHOICE` | escolha | `auto` | `auto` Automático<br>`fixed` Layout fiel<br>`reflow` Texto fluido | Automático analisa o arquivo e escolhe. Layout fiel mantém cada página idêntica ao original. Texto fluido extrai o texto para ele se adaptar à tela do leitor. |
| **Dispositivo**<br>`profile` | `--profile CHOICE` | escolha | `auto` | `auto` Automático (pelo formato)<br>`kindle-pw5` Kindle Paperwhite 5 / Signature<br>`kindle-pw6` Kindle Paperwhite (2024)<br>`kindle-colorsoft` Kindle Colorsoft<br>`kindle-basic` Kindle (2022/2024)<br>`kindle-pw4` Kindle Paperwhite 3/4 · Voyage<br>`kindle-oasis` Kindle Oasis<br>`kindle-scribe` Kindle Scribe<br>`kobo-clara` Kobo Clara 2E / BW<br>`kobo-clara-colour` Kobo Clara Colour<br>`kobo-libra` Kobo Libra 2<br>`kobo-libra-colour` Kobo Libra Colour<br>`kobo-sage` Kobo Sage<br>`kobo-elipsa` Kobo Elipsa 2E<br>`pocketbook-era` PocketBook Era<br>`remarkable` reMarkable 2<br>`tablet` Tablet / iPad<br>`phone` Celular<br>`original` Original (tamanho do arquivo) | Tela de destino. Define a resolução das páginas e se o leitor é colorido ou e-ink. |
| **Qualidade das páginas**<br>`quality` | `--quality CHOICE` | escolha | `alta` | `maxima` Máxima (sem perdas)<br>`alta` Alta<br>`equilibrada` Equilibrada<br>`compacta` Compacta | Compressão das imagens de página no layout fiel. |
| **Cores**<br>`color_mode` | `--color-mode CHOICE` | escolha | `auto` | `auto` Automático<br>`color` Colorido<br>`grayscale` Tons de cinza | Automático usa tons de cinza em telas e-ink monocromáticas e cor nas demais. |
| **Resolução (DPI)**<br>`dpi` | `--dpi INT` | inteiro | `0` | 0 a 600 | Usado quando o dispositivo é 'Original'. 0 escolhe automaticamente (200 DPI). |
| **Páginas**<br>`page_range` | `--page-range STR` | texto | (vazio) | texto livre | Intervalo de páginas a converter, ex.: 1-20,25. Vazio converte tudo. |
| **Senha do PDF**<br>`password` | `--password STR` | texto | (vazio) | texto livre | Senha de abertura, se o PDF pedir uma. Não aparece nos relatórios (vira `•••`). |

## Página

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Temperatura (K)**<br>`temperature` | `--temperature INT` | inteiro | `6500` | 1900 a 10000 | Cor do papel. 6500 K é neutro; valores menores deixam a página mais quente (amarelada), maiores deixam mais fria (azulada). |
| **Brilho**<br>`brightness` | `--brightness INT` | inteiro | `0` | -100 a 100 | De -100 a 100. |
| **Contraste**<br>`contrast` | `--contrast INT` | inteiro | `0` | -100 a 100 | De -100 a 100. |
| **Gama**<br>`gamma` | `--gamma FLOAT` | decimal | `1.0` | 0.3 a 3 | Acima de 1 escurece os meios-tons (texto fica mais encorpado em e-ink). |
| **Saturação**<br>`saturation` | `--saturation INT` | inteiro | `0` | -100 a 100 | De -100 a 100. |
| **Modo noturno**<br>`invert` | `--invert`<br>`--no-invert` | sim/não | não | sim ou não | Inverte claro e escuro mantendo as cores (fundo escuro, texto claro). |
| **Nitidez**<br>`sharpen` | `--sharpen`<br>`--no-sharpen` | sim/não | não | sim ou não | Realça bordas. Útil em PDFs digitalizados. |
| **Normalizar contraste**<br>`normalize` | `--normalize`<br>`--no-normalize` | sim/não | não | sim ou não | Estica os tons para usar do preto ao branco. Útil em digitalizações apagadas. |
| **Margens**<br>`trim_margins` | `--trim-margins CHOICE` | escolha | `none` | `none` Não cortar<br>`uniform` Corte uniforme<br>`page` Corte por página | Cortar margens deixa o texto maior na tela. O corte uniforme usa a mesma área em todas as páginas (separando pares e ímpares) para o tamanho da letra não variar. |
| **Dividir páginas duplas**<br>`split_spreads` | `--split-spreads`<br>`--no-split-spreads` | sim/não | não | sim ou não | Páginas mais largas que altas viram duas, na ordem de leitura. |
| **Preencher a tela**<br>`fill_screen` | `--fill-screen`<br>`--no-fill-screen` | sim/não | sim | sim ou não | Completa a página até o tamanho exato da tela com a cor do papel, sem faixas. |
| **Ajustar imagens do texto fluido**<br>`apply_to_images` | `--apply-to-images`<br>`--no-apply-to-images` | sim/não | não | sim ou não | No texto fluido, aplica temperatura e demais ajustes também às imagens. |

## Texto

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Remover cabeçalhos e rodapés**<br>`remove_headers_footers` | `--remove-headers-footers`<br>`--no-remove-headers-footers` | sim/não | sim | sim ou não | Detecta linhas repetidas no topo e no pé das páginas (títulos correntes, números). |
| **Juntar palavras hifenizadas**<br>`dehyphenate` | `--dehyphenate`<br>`--no-dehyphenate` | sim/não | sim | sim ou não | Junta palavras quebradas no fim da linha, mantendo o hífen de palavras compostas e de pronomes (ex.: disse-lhe). |
| **Detectar capítulos**<br>`detect_chapters` | `--detect-chapters`<br>`--no-detect-chapters` | sim/não | sim | sim ou não | Reconhece Capítulo, Parte, Prólogo, Epílogo etc. em português, inglês e espanhol. |
| **Padrão extra de capítulo**<br>`chapter_pattern` | `--chapter-pattern STR` | texto | (vazio) | texto livre | Expressão regular adicional para reconhecer títulos de capítulo. |
| **Manter números de página**<br>`keep_page_numbers` | `--keep-page-numbers`<br>`--no-keep-page-numbers` | sim/não | sim | sim ou não | Guarda a paginação do original (lista de páginas do EPUB 3). |
| **Tabelas**<br>`tables` | `--tables CHOICE` | escolha | `html` | `html` Tabela de verdade<br>`image` Imagem<br>`text` Texto corrido | Como levar tabelas do PDF para o texto fluido. |
| **Gráficos vetoriais como imagem**<br>`vector_graphics` | `--vector-graphics`<br>`--no-vector-graphics` | sim/não | sim | sim ou não | Gráficos desenhados no PDF (não são imagens) viram figuras em vez de sumirem. |
| **Alinhamento**<br>`text_align` | `--text-align CHOICE` | escolha | `justify` | `justify` Justificado<br>`left` À esquerda | Alinhamento sugerido ao leitor. |
| **Parágrafos**<br>`paragraph_style` | `--paragraph-style CHOICE` | escolha | `indent` | `indent` Recuo<br>`spaced` Espaçado | Recuo na primeira linha (estilo de livro) ou espaço entre parágrafos. |
| **Página da capa**<br>`cover_page` | `--cover-page INT` | inteiro | `1` | 1 a 100000 | Página do PDF usada como capa. |

## Kindle

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Aparecer no Kindle como**<br>`kindle_doc_type` | `--kindle-doc-type CHOICE` | escolha | `EBOK` | `EBOK` Livro<br>`PDOC` Documento pessoal | Livro aparece em Livros; Documento aparece em Documentos. |
| **Motor AZW3/MOBI**<br>`kindle_engine` | `--kindle-engine CHOICE` | escolha | `native` | `native` Nativo<br>`calibre` Calibre (se instalado) | Nativo é o gerador do Prelo. Calibre usa o ebook-convert instalado. |
| **Sumário no fim do livro**<br>`inline_toc` | `--inline-toc CHOICE` | escolha | `auto` | `auto` Automático<br>`yes` Sim<br>`no` Não | Automático adiciona no MOBI (que não tem menu de capítulos) e omite no AZW3. |

## PDF

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Manter texto pesquisável**<br>`pdf_text_layer` | `--pdf-text-layer`<br>`--no-pdf-text-layer` | sim/não | sim | sim ou não | Na saída PDF, mantém o texto original por baixo das páginas ajustadas. |

## Avançado

| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |
|---|---|---|---|---|---|
| **Pasta de depuração**<br>`debug_pipeline` | `--debug-pipeline STR` | texto | (vazio) | texto livre | Salva o livro em cada etapa (entrada, transformações, saída) nesta pasta. **Recusada quando chega pela rede** (modo servidor). |
| **Processos de renderização**<br>`workers` | `--workers INT` | inteiro | `0` | 0 a 32 | 0 usa o número de núcleos disponíveis. |

