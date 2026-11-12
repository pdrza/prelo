# API HTTP

Tudo o que a janela do Prelo faz passa por uma API HTTP local: a janela é só mais um cliente. Scripts e outros programas usam a mesma API. A documentação interativa (OpenAPI) fica em `/api/docs`, e o esquema em `/api/openapi.json`.

## Como subir

| Jeito | Modo | Endereço | Token |
|---|---|---|---|
| Aplicativo (`prelo-app`) | `desktop` | `127.0.0.1`, numa porta livre sorteada | aleatório a cada sessão, entregue à janela por cookie |
| `prelo serve` | `server` | `127.0.0.1:8765` (mude com `--host` e `--port`) | aleatório, impresso no terminal (ou `--token VALOR`) |
| `prelo serve --local-paths` | `desktop`, sem janela | idem | idem |
| `prelo serve --no-token` | qualquer um | idem | nenhum (só para desenvolvimento) |

O aplicativo também grava a porta e o token em `$XDG_RUNTIME_DIR/prelo/app.json` no Linux e em `%TEMP%\prelo\app.json` no Windows (pastas só do usuário), para outra instância do `prelo-app` conseguir entregar arquivos à janela aberta.

### Diferenças entre os modos

| | `desktop` | `server` |
|---|---|---|
| Adicionar pelo caminho (`POST /api/books`) | sim | não (403): só upload |
| Escolher a pasta de saída (`output_dir`) | sim | não (403); os arquivos vão para `outputs/` na pasta de dados |
| `output_dir` em `PUT /api/settings` | aceito | ignorado |
| Opções perigosas (`debug_pipeline`) | aceitas | recusadas e registradas nas notas da conversão; nem aparecem em `GET /api/options` |
| Mostrar na pasta, encerrar o app | sim (encerrar só com a janela) | não (403) |
| Abrir a pasta ao concluir | conforme a preferência | nunca |

O modo `server` existe para usar o Prelo como serviço. Mesmo assim, ele escuta só em `127.0.0.1` por padrão. Para expor na rede (`--host 0.0.0.0`), mantenha o token e ponha um proxy com HTTPS na frente.

## Autenticação

Com token, toda rota `/api/` (menos `GET /api/health`) exige um destes:

- cabeçalho `X-Prelo-Token: <token>`;
- cookie `prelo_token`, que o navegador recebe ao abrir `http://127.0.0.1:PORTA/?token=<token>` (o Prelo grava o cookie `HttpOnly` e `SameSite=Strict` e redireciona para `/`).

Sem token válido, a resposta é `401` com `{"code": "nao_autorizado", ...}`. A comparação do token é feita em tempo constante.

## Rotas

<!-- gerado:rotas -->
| Método | Rota | Grupo | O que faz |
|---|---|---|---|
| `GET` | `/api/health` | geral | Estado da API: versão, modo (`desktop`/`server`), se o Calibre foi encontrado e se aceita caminhos locais. Não exige token. |
| `GET` | `/api/formats` | geral | Formatos de entrada (com extensões e modos) e de saída. |
| `GET` | `/api/profiles` | geral | Dispositivos, com resolução, cor/e-ink e formato preferido. |
| `GET` | `/api/options` | geral | Todas as opções de conversão, com tipo, padrão, faixa e ajuda. No modo servidor, as perigosas não aparecem. |
| `GET` | `/api/books` | livros | Livros da estante, do mais recente para o mais antigo. |
| `POST` | `/api/books` | livros | Adiciona arquivos pelo caminho (`{"paths": [...]}`). **Só no modo desktop.** |
| `POST` | `/api/books/upload` | livros | Envia arquivos (multipart, campo `files`) e os coloca na estante. |
| `GET` | `/api/books/{book_id}` | livros | Um livro da estante. |
| `PATCH` | `/api/books/{book_id}` | livros | Salva metadados editados (`metadata`) e/ou opções do livro (`settings`, nível HIGH). |
| `DELETE` | `/api/books/{book_id}` | livros | Tira o livro da estante. O arquivo original não é apagado (só a cópia enviada por upload). |
| `GET` | `/api/books/{book_id}/cover` | livros | Capa em JPEG. |
| `GET` | `/api/books/{book_id}/pages` | livros | Número de páginas que a prévia consegue mostrar. |
| `GET` | `/api/books/{book_id}/pages/{page}/preview` | livros | Página `page` (a partir de 1) em JPEG, com os ajustes da query: `w`, `original`, `temperature`, `brightness`, `contrast`, `gamma`, `saturation`, `invert`, `grayscale`, `sharpen`, `normalize`. |
| `GET` | `/api/books/{book_id}/reader` | leitor | Leitor: abre o livro paginado para uma página de `w` × `h` pixels e letra `fs` (livros fluidos). Devolve o tipo (`reflow` ou `fixed`), o número de páginas, o sumário, a proporção de cada página (livros fixos) e a página onde o leitor parou. |
| `GET` | `/api/books/{book_id}/reader/pages/{page}` | leitor | Página `page` do leitor em JPEG, na paginação de `w`, `h` e `fs`, com densidade `dpr` e os mesmos ajustes de página da prévia (o tema do leitor). Num livro fixo, `w` × `h` é a caixa onde a página cabe. |
| `GET` | `/api/books/{book_id}/reader/pages/{page}/links` | leitor | Links internos da página (sumário do livro, notas): área em frações da página e a página de destino. |
| `PUT` | `/api/books/{book_id}/reader/position` | leitor | Guarda onde o leitor parou (`page`, `w`, `h`, `fs`). Num livro fluido, a posição é o capítulo e as letras antes da página, e sobrevive a outra letra e a reabrir o app. |
| `POST` | `/api/jobs` | conversões | Cria uma conversão: `book_id`, `output_format`, `options` e, só no modo desktop, `output_dir`. |
| `GET` | `/api/jobs` | conversões | Conversões desta sessão, da mais nova para a mais antiga. |
| `GET` | `/api/jobs/{job_id}` | conversões | Uma conversão: estado, etapa, progresso, log, resultado ou erro. |
| `POST` | `/api/jobs/{job_id}/cancel` | conversões | Cancela (na fila: na hora; rodando: pede para parar e encerra o processo se preciso). |
| `DELETE` | `/api/jobs/{job_id}` | conversões | Tira da lista uma conversão que já terminou. |
| `GET` | `/api/jobs/{job_id}/download` | conversões | Baixa o arquivo convertido (409 se ainda não terminou, 410 se o arquivo sumiu). |
| `POST` | `/api/jobs/{job_id}/reveal` | conversões | Mostra o arquivo convertido no gerenciador de arquivos. **Só no modo desktop.** |
| `POST` | `/api/quit` | geral | Encerra o app (o mesmo que Prelo › Sair). **Só no modo desktop.** |
| `GET` | `/api/settings` | preferências | Preferências. |
| `PUT` | `/api/settings` | preferências | Altera preferências (só as chaves conhecidas; pela rede, `output_dir` é ignorado). |
<!-- /gerado:rotas -->

Há ainda `POST /api/desktop/open`, interna e fora do esquema: é por ela que um segundo `prelo-app livro.pdf` entrega o arquivo à janela que já está aberta (instância única).

## Objetos

### Livro (`/api/books`)

| Campo | O que é |
|---|---|
| `id` | identificador pelo **conteúdo** (hash de 16 caracteres): o mesmo arquivo nunca entra duas vezes, e o livro continua o mesmo se for renomeado ou movido |
| `path`, `name`, `size` | onde está o arquivo, nome e tamanho em bytes |
| `exists` | se o arquivo ainda está no caminho salvo |
| `format` | `pdf`, `epub`, `mobi` (livros Kindle), `comic` ou `image` |
| `title`, `authors`, `language` | lidos do arquivo |
| `pages` | páginas (PDF, quadrinhos) ou capítulos (EPUB, Kindle); 0 quando não se sabe |
| `analysis` | só PDF: a análise do modo automático (`recommended`, `reasons` e as medidas) |
| `has_cover`, `cover_ratio` | se há capa e a proporção altura/largura |
| `metadata` | metadados editados pelo usuário (valem por cima dos do arquivo na conversão) |
| `settings` | opções salvas para o livro (nível HIGH) |
| `conversions` | as últimas 30 conversões do livro (caminho, formato, modo, tamanho, tempo, data) |
| `reading` | onde o leitor parou: `page`, `pages`, `fraction` (de 0 a 1), `font_size` e `updated_at`; nos livros fluidos, também `chapter` e `offset` (letras antes da página), que valem para qualquer letra e tamanho de janela. Vazio se o livro nunca foi lido |
| `uploaded`, `added_at` | se veio por upload e quando entrou na estante |

### Conversão (`/api/jobs`)

| Campo | O que é |
|---|---|
| `id`, `book_id`, `title`, `output_format` | identificação |
| `options` | as opções pedidas nesta conversão |
| `status` | `queued`, `running`, `done`, `error` ou `cancelled` |
| `stage`, `progress`, `message` | etapa (`preparo`, `entrada`, `transformações`, `saída`, `final`), progresso de 0 a 1 e mensagem atual |
| `log` | mensagens da conversão (as últimas 400) |
| `result` | quando `done`: veja abaixo |
| `error` | quando `error`: `{code, message, hint}` |
| `created_at`, `started_at`, `finished_at` | horários (segundos desde 1970) |

O `result` traz:

| Campo | O que é |
|---|---|
| `output_path` | arquivo gerado |
| `output_format`, `layout` | formato e modo usado (`fixed` ou `reflow`) |
| `documents`, `size`, `seconds`, `title` | número de documentos internos, bytes, duração e título |
| `notes` | explicações (por exemplo, por que o modo automático escolheu o layout fiel) |
| `warnings` | avisos (por exemplo, temperatura sem efeito numa tela monocromática) |
| `options` | opções que diferem do padrão, com o valor, o nível, **de onde vieram** (`source`) e se estavam travadas (`locked`); a senha aparece como `•••` |
| `option_notes` | escolhas que não valeram: `travada` (uma exigência do formato ou da entrada ganhou), `ignorada` (uma recomendação de nível maior ganhou) ou `bloqueada` (opção perigosa vinda da rede) |

## Ciclo de uma conversão

1. `POST /api/jobs` valida o formato e as opções **na hora**: opção desconhecida ou valor inválido respondem `400` sem criar nada.
2. A conversão entra na fila (`queued`). Rodam ao mesmo tempo tantas quantas a preferência *Conversões ao mesmo tempo* permite (1 a 4).
3. Cada conversão roda num **processo próprio**, criado do zero (`spawn`). Se esse processo morrer sem avisar, a conversão vira `error` com o código `processo_interrompido`; nada fica "rodando para sempre".
4. Terminada, a conversão é anotada no histórico do livro e, se a preferência estiver ligada, a pasta é aberta com o arquivo selecionado.
5. **Cancelar**: na fila, cancela na hora. Rodando, o Prelo pede para o processo parar e, se ele não parar em 8 segundos, encerra o processo.

A lista de conversões fica na memória da sessão (some ao fechar o app); o histórico de cada livro fica salvo na estante.

## Erros

Todo erro é um JSON `{"code": ..., "message": ..., "hint": ...}`, com a mensagem em português pronta para mostrar ao usuário e uma dica do que fazer.

| Status | Quando |
|---|---|
| `400` | erro do Prelo (códigos abaixo) |
| `401` | token ausente ou inválido (`nao_autorizado`) |
| `403` | recurso só do modo desktop, ou pasta de saída pedida pela rede |
| `404` | livro ou conversão não encontrados (`nao_encontrado`), ou livro sem capa |
| `409` | baixar ou mostrar uma conversão que ainda não terminou |
| `410` | o arquivo convertido não existe mais |
| `422` | o corpo da requisição não tem os campos esperados (validação do FastAPI) |
| `500` | erro inesperado (`erro_interno`): o tipo e a mensagem vão no `hint` e o rastro completo vai para o `prelo.log` |

| Código | Significado |
|---|---|
| `formato_nao_suportado` | o arquivo não é de um formato que o Prelo lê (ou é DOCX, CBR, KFX, Topaz...) |
| `conversao_incompativel` | a entrada não combina com a saída (quadrinho para TXT, texto fluido de imagens) |
| `drm` | o livro tem DRM |
| `senha_necessaria` | o PDF pede senha |
| `opcao_desconhecida` | nome de opção errado; a dica sugere o nome mais parecido |
| `opcao_invalida` | valor fora da faixa ou de tipo errado |
| `arquivo_inseguro` | ZIP com caminho absoluto, `..`, link simbólico, arquivos demais ou taxa de compressão suspeita |
| `falha_na_conversao` | a conversão não conseguiu terminar (arquivo corrompido, saída vazia...) |
| `cancelado` | a conversão foi cancelada |
| `processo_interrompido` | o processo da conversão parou sem avisar |

## Exemplos

```bash
API=http://127.0.0.1:8765
TOKEN=...   # impresso pelo "prelo serve"

# enviar um livro para a estante
curl -s -H "X-Prelo-Token: $TOKEN" -F "files=@livro.pdf" $API/api/books/upload

# converter para AZW3, layout fiel, página mais quente
curl -s -X POST $API/api/jobs -H "X-Prelo-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"book_id": "ID_DO_LIVRO", "output_format": "azw3",
       "options": {"profile": "kindle-pw5", "mode": "fixed", "temperature": 4800}}'

# acompanhar e baixar
curl -s -H "X-Prelo-Token: $TOKEN" $API/api/jobs/ID_DA_CONVERSAO
curl -s -H "X-Prelo-Token: $TOKEN" -OJ $API/api/jobs/ID_DA_CONVERSAO/download

# prévia de uma página com papel sépia
curl -s -H "X-Prelo-Token: $TOKEN" -o pagina.jpg \
  "$API/api/books/ID_DO_LIVRO/pages/3/preview?w=900&temperature=4300&saturation=-25"
```

As opções aceitas em `options` são as de [opcoes.md](opcoes.md), com os mesmos nomes.
