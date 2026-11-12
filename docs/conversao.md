# Como uma conversão funciona

O caminho de um livro dentro do Prelo, da entrada ao arquivo gravado: as etapas, o livro intermediário, como o modo de um PDF é escolhido e como cada opção chega ao valor final. Os formatos aceitos estão em [formatos.md](formatos.md) e a lista de opções em [opcoes.md](opcoes.md).

## Etapas

| Etapa | Progresso | O que acontece |
|---|---|---|
| preparo | 0 a 3 % | O formato é detectado pelo conteúdo, a combinação de entrada e saída é conferida e o arquivo é copiado para uma pasta temporária. O original nunca é aberto para escrita. |
| entrada | 3 a 70 % | O leitor do formato monta o livro intermediário. No layout fiel, as páginas são renderizadas aqui, em vários processos. |
| transformações | 70 a 76 % | Metadados editados pelo usuário, remoção de arquivos sem referência e conferência do sumário. |
| saída | 76 a 98 % | O escritor grava o formato pedido num arquivo temporário. |
| final | 98 a 100 % | O temporário vira o arquivo definitivo, sem sobrescrever nada: se o nome já existe, o novo ganha "(2)". |

Cada conversão roda num processo próprio. Se ele morrer, a conversão vira erro e a janela continua funcionando.

## O livro intermediário

Nenhum formato é convertido direto em outro. Cada leitor (`src/prelo/inputs/`) monta um `Book` (`src/prelo/core/model.py`) e cada escritor (`src/prelo/outputs/`) parte dele. Por isso qualquer entrada combina com qualquer saída sem um conversor por par, e as transformações são escritas uma vez só.

O `Book` guarda:

- os metadados: título, autores, idioma, editora, descrição, ISBN, assuntos, data, série e direitos;
- o **layout**: `fixed`, em que cada documento é uma página do tamanho da tela de destino, ou `reflow`, em que o texto se adapta à tela;
- a direção de leitura: da esquerda para a direita ou da direita para a esquerda (mangá);
- os documentos em XHTML, na ordem de leitura, e os recursos (imagens, CSS e fontes), que ficam em disco;
- o sumário, a lista de páginas do original e a capa.

O layout é a diferença em relação ao livro intermediário do Calibre, e é o que permite levar um PDF para AZW3 ou EPUB sem quebrar a diagramação: no layout fiel, cada página do PDF vira uma imagem na resolução exata da tela, e o escritor marca o livro como de layout fixo no formato de saída.

Para depurar, a opção *Pasta de depuração* (`--debug-pipeline PASTA`) grava o livro intermediário em EPUB depois da entrada e depois das transformações, junto com as opções efetivas.

## Análise automática do PDF

Com o modo em **Automático** (o padrão), o Prelo examina até 40 páginas espalhadas pelo PDF. Qualquer um destes sinais leva ao layout fiel:

| Sinal | A partir de |
|---|---|
| Páginas digitalizadas: uma imagem cobre mais de 60 % da página e há menos de 80 caracteres de texto | 30 % das páginas |
| Texto ilegível: fontes sem mapeamento de caracteres, que extraídas virariam lixo | 5 % do texto |
| Páginas dominadas por imagens (mais de 35 % da área) | 40 % das páginas |
| Várias colunas | 25 % das páginas |
| Gráficos ou diagramas vetoriais (mais de 250 desenhos na página) | 20 % das páginas |
| Pouco texto: menos de 250 caracteres por página, em média | quando nenhum outro sinal apareceu |

Sem nenhum desses sinais, o livro é de texto corrido em uma coluna e vai para o texto fluido. O motivo aparece ao abrir o livro na estante e no log da conversão, e a análise completa fica no campo `analysis` do livro na API.

De PDF para PDF, o automático sempre usa o layout fiel: as páginas são mantidas e só recebem os ajustes.

## Prioridade das opções

Cada opção tem um valor e um nível: LOW, MED ou HIGH. As camadas entram nesta ordem, e cada uma só substitui o valor anterior se tiver nível igual ou maior:

1. o padrão do código (LOW);
2. as recomendações da entrada: um EPUB de layout fixo recomenda o layout fiel (MED), e quadrinhos e imagens exigem layout fiel (HIGH);
3. as exigências da saída: CBZ exige layout fiel e TXT exige texto fluido (HIGH);
4. as recomendações do dispositivo: telas e-ink monocromáticas recomendam tons de cinza (MED);
5. as preferências salvas (LOW);
6. a configuração salva para o livro (HIGH);
7. a escolha feita na hora, pela janela, pela linha de comando ou pela API (HIGH).

Uma exigência HIGH da entrada ou da saída **trava** a opção, e nada a substitui. É assim que um quadrinho nunca vira texto fluido.

Nenhuma escolha some em silêncio. Quando uma opção pedida não vale, o resultado da conversão registra o motivo em `option_notes`:

- `travada`: uma exigência do formato ganhou;
- `ignorada`: uma recomendação de nível maior ganhou;
- `bloqueada`: uma opção perigosa chegou pela rede (modo servidor).

Nome de opção desconhecido é erro, com a sugestão do nome mais parecido. O resultado também lista as opções que diferem do padrão, com o valor, o nível e de onde veio cada uma; a janela Fila mostra a mesma lista.
