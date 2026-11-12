"""Perfis de dispositivo.

Como os "perfis de saída" do Calibre, mas com as resoluções reais das telas
atuais. No layout fiel as páginas são renderizadas exatamente nessa
resolução: é o melhor equilíbrio entre nitidez e tamanho de arquivo, porque o
leitor não precisa reescalar nada.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DeviceProfile:
    id: str
    name: str
    family: str
    width: int | None
    height: int | None
    color: bool
    eink: bool
    preferred_format: str

    @property
    def size(self) -> tuple[int, int] | None:
        if self.width and self.height:
            return (self.width, self.height)
        return None

    def recommendations(self):
        from .options import Level, Recommendation

        recs = []
        if self.eink and not self.color:
            # Tela monocromática: cinza deixa o arquivo menor e o resultado é o mesmo.
            recs.append(Recommendation("color_mode", "grayscale", Level.MED))
        return recs

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile("kindle-pw5", "Kindle Paperwhite 5 / Signature", "Kindle", 1236, 1648, False, True, "azw3"),
    DeviceProfile("kindle-pw6", "Kindle Paperwhite (2024)", "Kindle", 1264, 1680, False, True, "azw3"),
    DeviceProfile("kindle-colorsoft", "Kindle Colorsoft", "Kindle", 1264, 1680, True, True, "azw3"),
    DeviceProfile("kindle-basic", "Kindle (2022/2024)", "Kindle", 1072, 1448, False, True, "azw3"),
    DeviceProfile("kindle-pw4", "Kindle Paperwhite 3/4 · Voyage", "Kindle", 1072, 1448, False, True, "azw3"),
    DeviceProfile("kindle-oasis", "Kindle Oasis", "Kindle", 1264, 1680, False, True, "azw3"),
    DeviceProfile("kindle-scribe", "Kindle Scribe", "Kindle", 1860, 2480, False, True, "azw3"),
    DeviceProfile("kobo-clara", "Kobo Clara 2E / BW", "Kobo", 1072, 1448, False, True, "epub"),
    DeviceProfile("kobo-clara-colour", "Kobo Clara Colour", "Kobo", 1072, 1448, True, True, "epub"),
    DeviceProfile("kobo-libra", "Kobo Libra 2", "Kobo", 1264, 1680, False, True, "epub"),
    DeviceProfile("kobo-libra-colour", "Kobo Libra Colour", "Kobo", 1264, 1680, True, True, "epub"),
    DeviceProfile("kobo-sage", "Kobo Sage", "Kobo", 1440, 1920, False, True, "epub"),
    DeviceProfile("kobo-elipsa", "Kobo Elipsa 2E", "Kobo", 1404, 1872, False, True, "epub"),
    DeviceProfile("pocketbook-era", "PocketBook Era", "PocketBook", 1264, 1680, False, True, "epub"),
    DeviceProfile("remarkable", "reMarkable 2", "reMarkable", 1404, 1872, False, True, "pdf"),
    DeviceProfile("tablet", "Tablet / iPad", "Tela colorida", 1640, 2360, True, False, "epub"),
    DeviceProfile("phone", "Celular", "Tela colorida", 1080, 2160, True, False, "epub"),
    DeviceProfile("original", "Original (tamanho do arquivo)", "Tela colorida", None, None, True, False, "pdf"),
)

_BY_ID = {p.id: p for p in PROFILES}

# Dispositivo usado quando o perfil é "auto", conforme o formato de saída.
DEFAULT_FOR_FORMAT = {
    "azw3": "kindle-pw5",
    "mobi": "kindle-pw5",
    "epub": "tablet",
    "cbz": "tablet",
    "pdf": "original",
    "txt": "original",
}


def get_profile(profile_id: str, output_format: str | None = None) -> DeviceProfile:
    if profile_id in ("", "auto", None):
        profile_id = DEFAULT_FOR_FORMAT.get(output_format or "", "tablet")
    try:
        return _BY_ID[profile_id]
    except KeyError:
        raise KeyError(f"perfil desconhecido: {profile_id}") from None
