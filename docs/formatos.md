# Formatos

O Prelo lê PDF, EPUB, livros Kindle (AZW3/MOBI), quadrinhos e imagens, e grava EPUB, AZW3, MOBI, CBZ, PDF e TXT. Nenhum formato é convertido direto em outro: todo leitor monta o [livro intermediário](conversao.md#o-livro-intermediário) e todo escritor parte dele. Por isso qualquer entrada combina com qualquer saída, menos quando o conteúdo não permite (um quadrinho não tem texto para virar TXT).

<!-- gerado:formatos -->
### Entrada

| Formato | Extensões | Modos possíveis |
|---|---|---|
| PDF | `.pdf` | layout fiel, texto fluido |
| EPUB | `.epub`, `.kepub` | layout fiel, texto fluido |
| Quadrinhos (CBZ/ZIP) | `.cbz`, `.zip` | layout fiel |
| Imagem | `.bmp`, `.gif`, `.jpeg`, `.jpg`, `.png`, `.tif`, `.tiff`, `.webp` | layout fiel |
| Kindle (MOBI/AZW3) | `.azw3`, `.mobi`, `.azw`, `.prc`, `.pdb` | layout fiel, texto fluido |

### Saída

| Formato | Extensão | Modos | Para quê |
|---|---|---|---|
| EPUB | `.epub` | layout fiel, texto fluido | Padrão aberto. Kobo, Apple Books, Google Play, PocketBook e Envio para Kindle. |
| AZW3 | `.azw3` | layout fiel, texto fluido | Formato Kindle moderno (KF8). Copie pelo cabo USB para a pasta documents. |
| MOBI | `.mobi` | layout fiel, texto fluido | Kindle antigo (MOBI 6). Compatível com todos os Kindles, sem CSS. |
| CBZ | `.cbz` | layout fiel | Páginas como imagens. Ideal para quadrinhos e leitores de imagem. |
| PDF | `.pdf` | layout fiel, texto fluido | PDF com as páginas ajustadas (temperatura, margens) ou paginado de novo. |
| TXT | `.txt` | texto fluido | Só o texto, sem formatação. |

### O que combina com o quê

| Entrada \ Saída | EPUB | AZW3 | MOBI | CBZ | PDF | TXT |
|---|---|---|---|---|---|---|
| PDF | sim | sim | sim | sim | sim | sim |
| EPUB | sim | sim | sim | sim | sim | sim |
| Quadrinhos (CBZ/ZIP) | sim | sim | sim | sim | sim | não |
| Imagem | sim | sim | sim | sim | sim | não |
| Kindle (MOBI/AZW3) | sim | sim | sim | sim | sim | sim |

### Filtros do seletor de arquivos

| Filtro | Extensões |
|---|---|
| Livros | `.azw` `.azw3` `.bmp` `.cbz` `.epub` `.gif` `.jpeg` `.jpg` `.kepub` `.mobi` `.pdb` `.pdf` `.png` `.prc` `.tif` `.tiff` `.webp` `.zip` |
| Livros EPUB | `.epub` `.kepub` |
| Livros para Kindle | `.azw3` `.mobi` `.azw` `.prc` |
| Livros PDF | `.pdf` |
| Quadrinhos | `.cbz` `.zip` |
| Imagens | `.bmp` `.gif` `.jpeg` `.jpg` `.png` `.tif` `.tiff` `.webp` |
<!-- /gerado:formatos -->

## Como o formato é reconhecido

O formato vem do **conteúdo** do arquivo, não da extensão. Um `livro.pdf` que na verdade é um EPUB é lido como EPUB. A extensão só serve para os filtros do seletor e do arrastar.

| O que o Prelo encontra no início do arquivo | Resultado |
|---|---|
| `%PDF-` nos primeiros 1024 bytes | PDF |
| ZIP com `mimetype` = `application/epub+zip`, ou com um `.opf` e `META-INF/container.xml` | EPUB |
| ZIP com pasta `word/` | recusado: "DOCX ainda não é suportado como entrada" |
| ZIP com imagens | quadrinho (CBZ) |
| ZIP sem livro nem imagens | recusado |
| `Rar!` | recusado: CBR (RAR) ainda não é suportado; a dica é recompactar em ZIP |
| Banco PalmDB `BOOKMOBI` ou `TEXtREAd` | livro Kindle (depois da checagem de DRM) |
| Outro PalmDB (eReader e afins) | recusado, com explicação |
| `TPZ0` | recusado: Topaz (AZW1/TPZ) |
| `CONT` ou `\xeaDRMION\xee` | recusado: KFX, com a dica de baixar o livro em AZW3 |
| Assinatura de JPEG, PNG, WebP, GIF, BMP ou TIFF | imagem |
| Arquivo vazio ou qualquer outra coisa | recusado: "Formato não reconhecido" |

## Entrada

### PDF

- Dois modos: **layout fiel** (cada página vira uma imagem na resolução da tela) e **texto fluido** (o texto é extraído e reorganizado). No modo **Automático**, uma análise escolhe; as regras estão em [conversao.md](conversao.md#análise-automática-do-pdf).
- PDF para PDF no modo automático sempre usa layout fiel: as páginas são mantidas e só recebem os ajustes.
- **Senha**: PDFs que pedem senha de abertura usam a opção *Senha do PDF* (`--password`). Sem ela, a conversão para com o erro `senha_necessaria`.
- **DRM**: PDFs com filtros de criptografia que não são o padrão (proteção de editora) são recusados. PDFs com restrições de uso definidas pelo autor convertem, com um lembrete no log para respeitar os direitos autorais.
- **Páginas**: *Páginas* (`--page-range`, ex.: `1-20,25`) converte só parte do livro.
- **Capa**: a página escolhida em *Página da capa* (`--cover-page`, padrão 1).
- **Sumário**: vem dos marcadores do PDF. Sem marcadores, o layout fiel procura títulos de capítulo no texto das páginas.

### EPUB

- EPUB 2 e 3, fluido ou de layout fixo (`rendition:layout pre-paginated`).
- Lê metadados do OPF (título, autores, idioma, editora, descrição, data, assuntos, direitos, ISBN, série do Calibre ou `belongs-to-collection`), sumário (EPUB 3 `nav` ou, sem ele, o NCX), lista de páginas e capa (propriedade `cover-image`, `meta name="cover"`, referência `cover` do guia ou uma primeira página que só tem uma imagem).
- **Layout fiel de um EPUB fluido**: o MuPDF pagina o livro na proporção da tela escolhida e cada página vira imagem.
- **DRM**: EPUB com `META-INF/rights.xml` (Adobe) ou com criptografia de conteúdo em `encryption.xml` é recusado. Fontes ofuscadas (algoritmos do IDPF e da Adobe para fontes) são permitidas, como no Calibre.

### Livros Kindle (AZW3, MOBI, AZW, PRC)

- **KF8 (AZW3)**: o formato atual. O Prelo remonta os arquivos XHTML a partir dos esqueletos e fragmentos, recupera o CSS dos fluxos da tabela FDST, as imagens, as fontes embutidas (descomprimidas e desembaralhadas), a capa (EXTH 201) e o sumário (índice NCX). Links internos `kindle:pos` voltam a apontar para o capítulo e o ponto certos.
- **MOBI 6**: o formato antigo. Capítulos saem das quebras `<mbp:pagebreak/>`, os links `filepos` viram âncoras, as imagens `recindex` voltam a ser arquivos e os atributos antigos (`height`, `width`, `align`, `<font>`, `<center>`) viram CSS. O sumário vem do índice NCX ou, sem ele, do sumário em HTML apontado pelo guia.
- **Arquivos mistos** (MOBI 6 + KF8 no mesmo arquivo, como o Calibre e o KindleGen geram): a parte KF8 é a lida, porque tem o CSS original.
- **Compressão**: nenhuma, PalmDOC e HUFF/CDIC.
- **Metadados** do EXTH: título, autores, editora, descrição, ISBN, assuntos, data, direitos e idioma. Autor gravado como "Sobrenome, Nome" (forma de catálogo comum nesses arquivos) vira "Nome Sobrenome".
- **Layout fixo** (EXTH `fixed-layout`): o livro continua de layout fixo, com a resolução original.
- **Layout fiel de um livro fluido**: o Prelo grava um EPUB intermediário e o pagina como um EPUB.
- **Recusados**: livros com DRM (inclusive na parte KF8 dos mistos), Topaz e KFX.

### Quadrinhos (CBZ ou ZIP de imagens) e imagens

- As páginas seguem a **ordem natural** dos nomes (`p2` antes de `p10`). Arquivos ocultos, `__MACOSX` e `Thumbs.db` são ignorados.
- `ComicInfo.xml` dá título (ou "série número", se não houver título), série, número, autores (roteiro e desenho), resumo, editora e a direção de leitura: `Manga = YesAndRightToLeft` liga a leitura da direita para a esquerda.
- Subpastas viram capítulos do sumário.
- Quadrinhos e imagens **só convertem em layout fiel**: é uma trava (recomendação HIGH), então pedir texto fluido não tem efeito e a conversão explica o porquê. Em quadrinhos, *Dividir páginas duplas* vem ligado e *Margens* vem em "não cortar" (recomendações MED, que a sua escolha substitui).
- Uma imagem solta vira um livro de uma página.

## Saída

### EPUB

- EPUB 3 com NCX, que leitores EPUB 2 também entendem.
- **Texto fluido**: capa em SVG (escala sem distorcer), sumário (`nav` e NCX), lista de páginas do original e marcos de navegação. Capítulos grandes são divididos abaixo de 200 KB, por causa do limite de 260 KB do Adobe Digital Editions.
- **Layout fixo**: `rendition:layout pre-paginated` mais os metadados equivalentes da Amazon (`fixed-layout`, `original-resolution`), para o "Enviar para Kindle" manter as páginas, e o arquivo de opções da Apple Books. Mangá recebe `page-progression-direction="rtl"`.

### AZW3 (KF8)

- Gerado pelo próprio Prelo, sem Calibre nem KindleGen. Passa no inspetor de MOBI do Calibre sem avisos (testado na suíte quando o Calibre está instalado).
- Imagens que não são JPEG, PNG ou GIF, que estão em CMYK ou que passam de 5 MB viram JPEG (lado máximo de 3200 px). SVG vira PNG.
- Só fontes TTF e OTF são embutidas; as outras são deixadas de lado, com um aviso no log.
- Capa e miniatura (330 × 470) para a biblioteca do Kindle.
- *Aparecer no Kindle como* (`--kindle-doc-type`): **Livro** (`EBOK`, vai para Livros) ou **Documento pessoal** (`PDOC`).
- Livros de layout fixo levam os metadados `fixed-layout` e `original-resolution`.

### MOBI (MOBI 6)

- Para Kindles antigos. Sem CSS, imagens em JPEG ou GIF. No layout fiel, cada página vira uma imagem ajustada à tela.
- *Sumário no fim do livro* (`--inline-toc`): no automático, o MOBI ganha um sumário em HTML no fim (ele não tem menu de capítulos); o AZW3 não precisa.

### CBZ

- Exige layout fiel: as páginas vão numeradas (`0001.png`, `0002.jpg`...) sem recompressão, com um `ComicInfo.xml` (título, série, número, autores, editora, resumo, idioma, número de páginas e `Manga`).

### PDF

- **Layout fiel**: cada página é a imagem ajustada. Com *Manter texto pesquisável* (`--pdf-text-layer`, padrão sim) e entrada em PDF, o texto original fica por baixo da imagem, então busca e seleção continuam funcionando. Páginas cortadas ou divididas (proporção diferente da original) ficam sem essa camada, e o log diz quantas.
- **Texto fluido**: o MuPDF pagina o texto na proporção da tela do dispositivo.
- Os dois levam sumário e metadados (título, autores, assunto, palavras-chave).

### TXT

- Só texto, sem formatação: título, autores e os parágrafos, separados por linha em branco. Exige texto fluido.

### Motor alternativo: Calibre

Com *Motor AZW3/MOBI* em **Calibre** (`--kindle-engine calibre`, ou em Preferências), o Prelo grava um EPUB intermediário e chama o `ebook-convert` instalado (MOBI sai misto, MOBI 6 + KF8). Limite de 30 minutos por livro. Sem o Calibre instalado, a conversão para com uma explicação.

## O que ainda não é suportado

DOCX, HTML, RTF, FB2, CBR (RAR), ZIP com livros dentro, KFX e Topaz como entrada. Todos são reconhecidos e recusados com uma explicação, em vez de um erro genérico.
