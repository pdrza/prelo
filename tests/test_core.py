"""Regras do núcleo: opções, segurança, imagem e texto."""

from __future__ import annotations

import zipfile

import pytest
from PIL import Image

from prelo.core import text as T
from prelo.core.errors import DrmError, InvalidOptionError, UnknownOptionError, UnsafeArchiveError
from prelo.core.formats import detect_format
from prelo.core.imaging import NEUTRAL_K, Adjustments, apply_adjustments, encode_page, white_point
from prelo.core.options import OPTIONS, Level, OptionSet, Recommendation, validate_recommendations
from prelo.core.safety import AtomicOutput, check_epub_drm, safe_extract, sanitize_filename, unique_path
from prelo.inputs.images import ComicInput, ImageInput
from prelo.core.pipeline import output_recommendations
from prelo.core.profiles import PROFILES


# ------------------------------------------------------------------ opções
def test_todas_as_recomendacoes_usam_opcoes_existentes(tmp_path):
    """O Calibre tem recomendações com nome errado que nunca valem
    (page_breaks_brefore). Aqui isso quebra o teste."""
    recs = ComicInput(tmp_path).recommendations() + ImageInput(tmp_path).recommendations()
    for fmt in ("epub", "azw3", "mobi", "cbz", "pdf", "txt"):
        recs += output_recommendations("pdf", fmt)
    for p in PROFILES:
        recs += p.recommendations()
    validate_recommendations(recs)


def test_nome_errado_de_opcao_e_erro_com_sugestao():
    with pytest.raises(UnknownOptionError) as exc:
        OptionSet().apply({"temperatura": 5000}, Level.HIGH, "teste")
    assert "temperature" in exc.value.hint


def test_valor_fora_da_faixa():
    with pytest.raises(InvalidOptionError):
        OptionSet().apply({"temperature": 99999}, Level.HIGH, "teste")


def test_recomendacao_high_trava_a_opcao():
    opts = OptionSet()
    opts.recommend([Recommendation("mode", "fixed", Level.HIGH)], "saída CBZ")
    opts.apply({"mode": "reflow"}, Level.HIGH, "escolha atual")
    assert opts.mode == "fixed"
    assert opts.notes and opts.notes[0]["kind"] == "travada"


def test_med_perde_para_escolha_do_usuario_mas_ganha_de_preferencia():
    opts = OptionSet()
    opts.recommend([Recommendation("color_mode", "grayscale", Level.MED)], "dispositivo")
    opts.apply({"color_mode": "color"}, Level.LOW, "preferências")
    assert opts.color_mode == "grayscale"
    opts.apply({"color_mode": "color"}, Level.HIGH, "escolha atual")
    assert opts.color_mode == "color"


def test_opcao_perigosa_bloqueada_pela_rede():
    opts = OptionSet()
    opts.apply({"debug_pipeline": "/tmp/x"}, Level.HIGH, "api", from_network=True)
    assert opts.debug_pipeline == ""
    assert opts.notes[0]["kind"] == "bloqueada"


def test_coerce_aceita_textos_da_cli():
    assert OPTIONS["invert"].coerce("sim") is True
    assert OPTIONS["gamma"].coerce("1.4") == 1.4


# --------------------------------------------------------------- segurança
def test_nome_de_arquivo_valido_em_fat32_e_exfat():
    assert sanitize_filename('CON') == "_CON"
    assert sanitize_filename('a<b>:c"d/e\\f|g?h*') == "a b c d e f g h"
    assert sanitize_filename("  título. ") == "título"
    assert len(sanitize_filename("x" * 400)) <= 120


def test_nunca_sobrescreve(tmp_path):
    (tmp_path / "Livro.epub").write_bytes(b"original")
    p = unique_path(tmp_path, "Livro", ".epub")
    assert p.name == "Livro (2).epub"


def test_gravacao_atomica_nao_deixa_lixo(tmp_path):
    dest = tmp_path / "saida.epub"
    with pytest.raises(RuntimeError):
        with AtomicOutput(dest) as tmp:
            tmp.write_bytes(b"meio")
            raise RuntimeError("falhou")
    assert list(tmp_path.iterdir()) == []


def test_saida_vazia_e_erro(tmp_path):
    from prelo.core.errors import ConversionError

    with pytest.raises(ConversionError):
        with AtomicOutput(tmp_path / "x.epub"):
            pass
    assert list(tmp_path.iterdir()) == []


def test_zip_slip_recusado(tmp_path):
    z = tmp_path / "mal.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../fora.txt", "x")
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, tmp_path / "dest")
    assert not (tmp_path.parent / "fora.txt").exists()


def test_epub_com_drm_recusado(tmp_path):
    (tmp_path / "META-INF").mkdir()
    (tmp_path / "META-INF" / "encryption.xml").write_text(
        '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
        "</EncryptedData></encryption>")
    with pytest.raises(DrmError):
        check_epub_drm(tmp_path)


def test_fontes_ofuscadas_sao_permitidas(tmp_path):
    (tmp_path / "META-INF").mkdir()
    (tmp_path / "META-INF" / "encryption.xml").write_text(
        '<encryption><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/></encryption>')
    check_epub_drm(tmp_path)


def test_formato_detectado_pelo_conteudo(tmp_path, corpus):
    disfarcado = tmp_path / "na-verdade-epub.pdf"
    disfarcado.write_bytes(corpus["epub"].read_bytes())
    assert detect_format(disfarcado) == "epub"
    assert detect_format(corpus["novel"]) == "pdf"
    assert detect_format(corpus["cbz"]) == "comic"


# ------------------------------------------------------------------ imagem
def test_temperatura_neutra_nao_muda_nada():
    img = Image.new("RGB", (8, 8), (200, 120, 40))
    assert white_point(NEUTRAL_K) == (1.0, 1.0, 1.0)
    assert apply_adjustments(img, Adjustments()).getpixel((0, 0)) == (200, 120, 40)


def test_temperatura_quente_aquece_o_papel_e_mantem_o_preto():
    page = Image.new("RGB", (4, 1), (255, 255, 255))
    page.putpixel((0, 0), (0, 0, 0))
    out = apply_adjustments(page, Adjustments(temperature=4000))
    r, g, b = out.getpixel((1, 0))
    assert r == 255 and r > g > b
    assert out.getpixel((0, 0)) == (0, 0, 0)


def test_temperatura_fria_azula():
    out = apply_adjustments(Image.new("RGB", (1, 1), (255, 255, 255)), Adjustments(temperature=9000))
    r, g, b = out.getpixel((0, 0))
    assert b == 255 and b > r


def test_modo_noturno_inverte_a_luminancia():
    out = apply_adjustments(Image.new("RGB", (1, 1), (255, 255, 255)), Adjustments(invert=True))
    assert max(out.getpixel((0, 0))) < 10


def test_tons_de_cinza_viram_png_de_16_tons():
    img = Image.linear_gradient("L").resize((300, 400))
    data, ext, _ = encode_page(img, "alta")
    assert ext == "png"
    import io
    with Image.open(io.BytesIO(data)) as im:
        assert len(im.getcolors(256)) <= 16


# ------------------------------------------------------------------ texto
def test_ligaduras_e_acentos_soltos():
    assert T.clean_text("ﬁm da ﬂor") == "fim da flor"
    assert T.clean_text("´agua") == "água"       # acento antes da vogal (pdftohtml)
    assert T.clean_text("a´gua") == "água"       # acento depois da vogal
    assert T.clean_text("ac¸ão") == "ação"  # cedilha solta


@pytest.mark.parametrize("left,right,expected", [
    ("conver-", "são", "conversão"),
    ("guarda-", "-chuva", "guarda-chuva"),   # hífen repetido na linha seguinte
    ("disse-", "lhe", "disse-lhe"),          # pronome oblíquo
    ("pós-", "Graduação", "pós-Graduação"),  # maiúscula: hífen de verdade
])
def test_hifenizacao_em_portugues(left, right, expected):
    joined, _ = T.join_hyphenated(left, right, set(), set())
    assert joined == expected


def test_vocabulario_do_livro_decide():
    plain, hyph = T.vocabulary("o guarda-chuva estava aqui")
    assert T.join_hyphenated("guarda-", "chuva", plain, hyph)[0] == "guarda-chuva"
    plain, hyph = T.vocabulary("os canos da casa")
    assert T.join_hyphenated("ca-", "nos", plain, hyph)[0] == "canos"


@pytest.mark.parametrize("title", ["Capítulo 1", "CAPÍTULO XII", "Capítulo Um", "Parte II", "Prólogo",
                                   "Epílogo", "Chapter 3", "Capítulo primeiro", "Introdução"])
def test_capitulos_em_portugues_e_ingles(title):
    assert T.is_chapter_title(title)


def test_frase_comum_nao_e_capitulo():
    assert not T.is_chapter_title("O capítulo mais difícil da vida dele começou ali.")
