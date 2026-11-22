"""QR-код для проверки подлинности электронной справки (SVG, без внешних сервисов)."""
import re

import qrcode
import qrcode.image.svg
from markupsafe import Markup


def qr_svg(data: str) -> Markup:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    svg = img.to_string(encoding="unicode")
    # убираем фиксированный размер в мм: размер задает CSS
    svg = re.sub(r'\swidth="[^"]+"\sheight="[^"]+"', "", svg, count=1)
    svg = svg.replace("<svg ", '<svg class="qr" role="img" aria-label="QR-код для проверки справки" ', 1)
    return Markup(svg)
