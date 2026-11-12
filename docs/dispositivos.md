<!-- Arquivo gerado por tools/gerar_docs.py a partir do código. Não edite à mão. -->

# Dispositivos

No layout fiel, cada página é renderizada **na resolução exata da tela** escolhida: o leitor não precisa reescalar nada, então fica nítido sem inflar o arquivo. O dispositivo também decide as cores: telas e-ink monocromáticas recomendam tons de cinza (nível MED, então a sua escolha vale mais que a recomendação).

| Id | Aparelho | Família | Resolução | Tela | Formato preferido |
|---|---|---|---|---|---|
| `kindle-pw5` | Kindle Paperwhite 5 / Signature | Kindle | 1236 × 1648 | e-ink monocromática | AZW3 |
| `kindle-pw6` | Kindle Paperwhite (2024) | Kindle | 1264 × 1680 | e-ink monocromática | AZW3 |
| `kindle-colorsoft` | Kindle Colorsoft | Kindle | 1264 × 1680 | e-ink colorida | AZW3 |
| `kindle-basic` | Kindle (2022/2024) | Kindle | 1072 × 1448 | e-ink monocromática | AZW3 |
| `kindle-pw4` | Kindle Paperwhite 3/4 · Voyage | Kindle | 1072 × 1448 | e-ink monocromática | AZW3 |
| `kindle-oasis` | Kindle Oasis | Kindle | 1264 × 1680 | e-ink monocromática | AZW3 |
| `kindle-scribe` | Kindle Scribe | Kindle | 1860 × 2480 | e-ink monocromática | AZW3 |
| `kobo-clara` | Kobo Clara 2E / BW | Kobo | 1072 × 1448 | e-ink monocromática | EPUB |
| `kobo-clara-colour` | Kobo Clara Colour | Kobo | 1072 × 1448 | e-ink colorida | EPUB |
| `kobo-libra` | Kobo Libra 2 | Kobo | 1264 × 1680 | e-ink monocromática | EPUB |
| `kobo-libra-colour` | Kobo Libra Colour | Kobo | 1264 × 1680 | e-ink colorida | EPUB |
| `kobo-sage` | Kobo Sage | Kobo | 1440 × 1920 | e-ink monocromática | EPUB |
| `kobo-elipsa` | Kobo Elipsa 2E | Kobo | 1404 × 1872 | e-ink monocromática | EPUB |
| `pocketbook-era` | PocketBook Era | PocketBook | 1264 × 1680 | e-ink monocromática | EPUB |
| `remarkable` | reMarkable 2 | reMarkable | 1404 × 1872 | e-ink monocromática | PDF |
| `tablet` | Tablet / iPad | Tela colorida | 1640 × 2360 | colorida | EPUB |
| `phone` | Celular | Tela colorida | 1080 × 2160 | colorida | EPUB |
| `original` | Original (tamanho do arquivo) | Tela colorida | tamanho do original | colorida | PDF |

## Dispositivo automático

Com o dispositivo em **Automático** (`--profile auto`, o padrão), o Prelo escolhe pelo formato de saída:

| Saída | Dispositivo usado |
|---|---|
| AZW3 | `kindle-pw5` (Kindle Paperwhite 5 / Signature) |
| MOBI | `kindle-pw5` (Kindle Paperwhite 5 / Signature) |
| EPUB | `tablet` (Tablet / iPad) |
| CBZ | `tablet` (Tablet / iPad) |
| PDF | `original` (Original (tamanho do arquivo)) |
| TXT | `original` (Original (tamanho do arquivo)) |

No perfil `original` não há tela de destino: as páginas são renderizadas pela opção **Resolução (DPI)** (`--dpi`, 0 = 200 DPI). Em qualquer perfil, nenhum lado de página passa de 4096 px.

O dispositivo padrão é escolhido na janela **Dispositivos** (vale para a janela Converter) e pode ser trocado em cada conversão.

