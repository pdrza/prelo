# Regras do Calibre no Prelo

O Prelo partiu de um estudo do motor de conversão do Calibre 9.18 (pipeline, regras, segurança e as 516 configurações). Este documento diz, regra por regra, o que foi **adotado**, o que foi **melhorado** e o que **não se aplica**, e onde está no código.

Resumo: a arquitetura (livro intermediário, prioridade de opções, processo separado, gravação atômica) foi adotada quase sem mudança, porque funciona. As melhorias se concentram onde o Calibre falha com mais frequência: **PDF como entrada**, **português**, **sobrescrita de arquivos** e **opções ignoradas em silêncio**.

## Arquitetura

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Nenhum formato vira outro direto: tudo passa pelo livro intermediário (OEB) | **Adotada.** O `Book` também sabe se é de layout fixo ou fluido, o que permite levar PDF para AZW3 sem quebrar a diagramação | `core/model.py` |
| Etapas com faixas de progresso (entrada 1–34 %, transformações 34–67 %, saída 67–100 %) | **Adotada**, com faixas reequilibradas: a renderização de páginas é a parte pesada (entrada 3–70 %) | `core/pipeline.py` |
| `--debug-pipeline` salva o livro em cada etapa | **Adotada** (`debug_pipeline`): salva o livro depois da entrada e depois das transformações, com as opções efetivas. Bloqueada quando vem da rede | `core/pipeline.py` |
| Remover do manifesto arquivos que nada referencia (transformação 15) | **Adotada**, incluindo referências dentro do CSS | `pipeline.remove_unreferenced` |
| Sem sumário, cria uma entrada única | **Adotada** ("Início") | `pipeline.ensure_toc` |

## Opções e prioridade

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Níveis LOW/MED/HIGH; camada só substitui com nível igual ou maior | **Adotada** na mesma ordem: padrão → plugins → dispositivo → preferências → configuração do livro → escolha atual | `core/options.py` |
| Recomendação HIGH de plugin trava a opção (quadrinhos sempre com margem 0) | **Adotada.** Ex.: saída CBZ trava o layout fixo; quadrinho não pode virar texto fluido | `pipeline.output_recommendations`, `inputs/images.py` |
| Recomendações com nome errado (`page_breaks_brefore`) são ignoradas em silêncio | **Melhorada.** Nome desconhecido é erro com sugestão do nome certo; um teste garante que nenhum plugin recomenda opção inexistente | `options.unknown_option`, `tests/test_core.py` |
| O usuário não sabe por que a escolha dele não valeu | **Melhorada.** Cada opção travada ou ignorada vira uma nota; a Fila mostra as opções efetivas e de onde vieram | `OptionSet.notes`, janela Fila |
| Configuração salva por livro (nível HIGH) | **Adotada.** O livro é identificado pelo conteúdo, não pelo caminho: a configuração sobrevive a renomear ou mover o arquivo | `core/library.py` |
| Cada opção tem um parâmetro de linha de comando | **Adotada.** O registro de opções gera a CLI, a API (`GET /api/options`) e a validação | `cli.py` |
| Perfis de saída (kindle, kobo, tablet...) | **Melhorada.** Perfis com a resolução real das telas atuais; telas e-ink monocromáticas recomendam tons de cinza (MED) | `core/profiles.py` |

## Pré-processamento e texto

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Ligaduras viram letras (ﬁ → fi) | **Adotada** | `core/text.py` |
| Acentos soltos do pdftohtml (´e → é) | **Adotada**, só em vogais, ç e ñ, para "a´gua" não virar "aǵua" | `text.fix_spacing_accents` |
| Juntar palavras hifenizadas consultando o próprio livro | **Melhorada** com regras do português: hífen repetido na linha seguinte indica composto (guarda-/-chuva); pronomes oblíquos mantêm o hífen (disse-lhe) | `text.join_hyphenated` |
| Cabeçalho e rodapé: pular N pixels ou expressão regular na primeira/última linha | **Melhorada.** Detecta linhas repetidas no topo e no pé ao longo do livro (números viram curinga), mais números de página | `pdf_reflow.find_running_lines` |
| Juntar linhas quebradas por estatística de comprimento (fator 0,45) | **Substituída** por geometria: recuo, espaço vertical, largura da última linha, mudança de tamanho; parágrafos que continuam na outra página ou coluna são unidos | `pdf_reflow.split_paragraphs`, `_merge_continuations` |
| Heurísticas desligadas por padrão porque podem piorar | **Mantido o espírito:** a análise automática só escolhe texto fluido para livros de uma coluna e texto corrido; o resto vai para layout fiel | `inputs/pdf.analyze` |

## Capítulos, sumário e fontes

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Detecção de capítulos só reconhece inglês (*chapter, book, part...*) | **Melhorada:** português, inglês e espanhol, inclusive números por extenso e romanos | `text.CHAPTER_RE` |
| Se o sumário gerado tiver menos de 2 entradas e o original mais de 2, o original volta | **Adotada:** os marcadores do PDF têm prioridade; títulos detectados só entram se os marcadores forem pobres | `pdf_reflow.build_reflow` |
| Tamanho base = o que cobre mais caracteres | **Adotada** para separar corpo de texto e títulos | `pdf_reflow.collect_stats` |
| Entrelinha mínima de 120 % | **Adotada** (CSS gerado com 1,4) | `pdf_reflow.BASE_CSS` |

## Saída

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| EPUB: dividir arquivos acima de 260 KB (Adobe Digital Editions) | **Adotada** (corte em 200 KB, na fronteira de parágrafo) | `pdf_reflow.MAX_DOC_BYTES` |
| EPUB 2 por padrão | **Melhorada:** EPUB 3 com NCX, que leitores EPUB 2 também entendem; layout fixo com os metadados da Amazon para o "Enviar para Kindle" manter as páginas | `outputs/epub.py` |
| Capa em SVG para escalar sem distorcer | **Adotada** no texto fluido | `epub.cover_document` |
| MOBI 6 por padrão; imagens convertidas para JPEG | **Adotada** | `outputs/mobi.py` |
| Sumário no fim do livro (não no início) | **Adotada** no MOBI; no AZW3 o menu de capítulos (NCX) já cumpre esse papel | opção `inline_toc` |
| AZW3: sem `aid` dentro de tabelas (o Kindle trava) | **Adotada** | `outputs/kf8.py` |
| AZW3: entradas do sumário ordenadas por (profundidade, posição); guia ordenado; TBS no fim dos registros | **Adotada**; o arquivo passa no inspetor de MOBI do Calibre sem avisos | `kf8.py`, `outputs/tbs.py` |
| AZW3/MOBI dependem do Calibre (ou do KindleGen, descontinuado) | **Melhorada:** gerador próprio; o Calibre fica como motor alternativo opcional | `outputs/kf8.py`, `calibre_engine.py` |
| PDF como entrada não suporta colunas, tabelas, imagens vetoriais, links nem sumário | **Melhorada:** modo layout fiel (páginas renderizadas na resolução da tela) e, no texto fluido, tabelas reais, gráficos vetoriais como figuras, links e sumário pelos marcadores | `inputs/pdf.py`, `pdf_reflow.py` |
| Quadrinhos: cinza, normalizar, nitidez, cortar, dividir páginas duplas, mangá | **Adotada** como opções de página; o modo mangá vem do `ComicInfo.xml` | `inputs/images.py` |

## Segurança

| Proteção do Calibre | No Prelo | Onde |
|---|---|---|
| Conversão em processo separado | **Adotada.** Se o processo morrer sem responder, o trabalho vira erro e a interface continua | `api/jobs.py`, `api/worker.py` |
| Trabalhar sobre cópias | **Adotada** | `safety.Workspace.copy_input` |
| Gravação atômica num temporário sem extensão | **Adotada** | `safety.AtomicOutput` |
| Saída vazia é erro | **Adotada** | `safety.AtomicOutput` |
| Guarda `ORIGINAL_EPUB` só na conversão para o mesmo formato | **Melhorada:** o Prelo **nunca sobrescreve**; o novo arquivo ganha "(2)". Isso fecha o risco "alto" de MOBI → EPUB apagar um EPUB existente | `safety.unique_path` |
| Recusar livros com DRM | **Adotada** para EPUB, PDF (filtros que não são o padrão) e MOBI/AZW | `safety.check_epub_drm`, `inputs/pdf.open_pdf`, `formats.raise_if_mobi_drm` |
| Extração de ZIP neutraliza caminhos absolutos e `..` | **Adotada e reforçada:** também recusa links simbólicos e "zip bombs" | `safety.safe_extract` |
| Servidor ignora opções perigosas vindas da rede | **Adotada:** no modo servidor, caminhos locais e `debug_pipeline` são recusados | `api/server.py`, `OptionSet.apply(from_network=True)` |
| Nomes de pasta curtos (40 caracteres no Windows) | **Adotada** para arquivos, pensando na memória dos leitores (FAT32/exFAT): caracteres proibidos, nomes reservados (CON, NUL...) e caminhos curtos | `safety.sanitize_filename` |
| Detectar formato pela extensão | **Melhorada:** pelo conteúdo do arquivo | `core/formats.py` |

## Adicionar livros

O "Adicionar livros" do Calibre 9.18 foi analisado a partir do programa instalado (o código Python vem compilado em `python-lib.bypy.frozen`; os módulos `calibre.gui2.actions.add`, `calibre.gui2.add` e `calibre.gui2.dnd` foram extraídos e desmontados).

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Seletor com o filtro "Livros" (todas as extensões menos as de arquivos compactados) e um filtro por família: EPUB, Kindle, PDF, HTML, texto, quadrinhos... (`get_filters`) | **Adotada** com as famílias que o Prelo lê: Livros, EPUB, Kindle, PDF, Quadrinhos, Imagens. Maiúsculas e minúsculas no Linux, porque o GTK diferencia (o seletor do Windows, não) | `formats.BOOK_FILTERS`, `desktop.book_filters` |
| Lembra a última pasta do diálogo (`add books dialog dir`) | **Adotada** (`last_add_dir`); na primeira vez abre em Downloads | `Bridge.choose_books` |
| Antes de ler, confere se o caminho existe e se há permissão (`validate_source`) | **Adotada**, com a mensagem de cada arquivo | `desktop.expand_paths` |
| Arrastar e soltar aceita URLs de arquivo, links http (que baixa) e as "promessas de arquivo" do Firefox, e filtra pela extensão (`dnd_get_files`, `get_firefox_rurl`) | **Adotada**: arquivos, `text/uri-list`, `text/x-moz-url` e links http(s) baixados com limite de 512 MB; se o caminho completo não chega pelo GTK (ou pelo WebView2, no Windows), a página usa as URLs ou envia o arquivo. Nada aceito vira um aviso, nunca silêncio | `main.js`, `Bridge.add_uris`, `download_book` |
| Adicionar pastas, com um ou vários livros por pasta | **Adotada** de forma simples: uma pasta arrastada é varrida atrás de livros (sem pastas ocultas) | `desktop.expand_paths` |
| Extrai ZIP/RAR/7Z e adiciona os livros de dentro | **Não adotada**: um ZIP de imagens é quadrinho; ZIP com livros dentro ainda não | |
| Duplicados por título e autor (`find_identical_books`), com pergunta ou fusão de formatos | **Mudada**: o Prelo identifica o livro pelo **conteúdo**, então o mesmo arquivo nunca entra duas vezes e o mesmo livro em outro formato (o MOBI e o EPUB) fica como item separado, já que cada um pode ser convertido | `core/library.py` |
| Lê os metadados num processo separado e junta as falhas num relatório no fim | **Adaptada**: cada falha vira um aviso com o motivo e a dica (DRM, KFX, permissão) | `main.js` (`booksAdded`) |

## Entrada Kindle (MOBI/AZW3)

| Regra do Calibre | No Prelo | Onde |
|---|---|---|
| Arquivo misto (MOBI 6 + KF8): lê a parte KF8 | **Adotada**; a fronteira vem do EXTH 121 | `inputs/mobi.KindleFile` |
| Recursos em duas faixas nos arquivos mistos; `kindle:embed` conta todos os registros da faixa e pula FLIS, FCIS, FDST, DATP, RESC... | **Adotada** | `KindleFile.resource_slots`, `Resources` |
| Fontes embutidas com zlib e os primeiros 1040 bytes embaralhados (XOR) | **Adotada** | `inputs/mobi._font` |
| Fragmento com posição de inserção errada: acha a tag pelo `aid` do seletor | **Adotada** | `KF8Reader._parts` |
| Compressão PalmDOC e HUFF/CDIC | **Adotada** (HUFF/CDIC em Python puro) | `HuffCdic` |
| Os fluxos de CSS/SVG seguem a tabela FDST | **Adotada**: há geradores que declaram no cabeçalho só o tamanho do texto principal, e cortar nele perderia o CSS | `KindleFile.text` |
| MOBI 6: âncoras nas posições `filepos`, capítulos nas quebras `mbp:pagebreak`, atributos antigos (`height`, `width`, `<font>`) viram CSS | **Adotada** | `Mobi6Reader` |
| Livro com DRM é recusado | **Adotada**, também na parte KF8 dos mistos; Topaz e KFX recebem mensagem própria | `formats.detect_format` |

## Modos de conversão

| Modo do Calibre | No Prelo | Onde |
|---|---|---|
| Individual (diálogo com todas as opções; salva as escolhas do livro) | **Adotado**: conversor completo, ajustes salvos por livro | janela Converter, `PATCH /api/books/{id}` |
| Em massa (uma configuração para todos) | **Adotado** na janela e em `prelo-lote`, que ainda lembra o que já converteu | `bin/prelo-lote` |
| Ao enviar para o dispositivo | **Adotado**: detecta Kindle/Kobo no USB, escolhe o formato, converte só o necessário, não duplica e ejeta | `bin/prelo-enviar` (Windows: `windows/bin/scripts/prelo-enviar.ps1`) |
| Pasta de adição automática | **Adotado** como pasta monitorada, que pode virar serviço do systemd (no Windows, tarefa agendada) | `bin/prelo-vigiar` (Windows: `windows/bin/scripts/prelo-vigiar.ps1`) |
| Linha de comando (`ebook-convert`) | **Adotado** (`prelo convert`), com `--print-path` para scripts | `cli.py` |
| Servidor de conteúdo | **Adotado** como API local (`prelo serve`), recusando opções perigosas vindas da rede | `api/server.py` |

## Não se aplica (ainda)

- Biblioteca em banco de dados, unidades de rede e sincronização: o Prelo guarda só a estante (`library.json`, gravação atômica) e não move os livros de lugar.
- Plugins de terceiros do tipo "arquivo" e envio por e-mail: fora do escopo por enquanto.
- DOCX, HTML, KFX e Topaz como entrada (os dois últimos são detectados e explicados ao adicionar).
