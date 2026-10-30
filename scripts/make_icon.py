"""Draws icon.ico: a dark console tile with two segmented meters (MAIN green,
SUB cyan) and the band-scale markers between them. Drawn at 1024 px and
downsampled for crisp small sizes."""
import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon.ico")

BG, EDGE = (11, 21, 30, 255), (67, 214, 230, 255)
GREEN, CYAN, RED = (95, 224, 124), (64, 210, 228), (224, 65, 58)
TEAL = (42, 161, 168)


def bars(d: ImageDraw.ImageDraw, y: int, color, lit: int, n: int = 9):
    x0, x1, h = 170, 854, 150
    gap = 18
    w = (x1 - x0 - gap * (n - 1)) / n
    for i in range(n):
        c = color if i < lit else tuple(v // 4 for v in color)
        if i >= n - 2 and i < lit:
            c = RED
        x = x0 + i * (w + gap)
        d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=c + (255,))


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=190, fill=BG, outline=EDGE, width=34)

glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
bars(g, 230, GREEN, 7)
bars(g, 644, CYAN, 5)
glow = glow.filter(ImageFilter.GaussianBlur(26))
img = Image.alpha_composite(img, glow)
d = ImageDraw.Draw(img)
bars(d, 230, GREEN, 7)
bars(d, 644, CYAN, 5)

# band scale line with the two radio markers
d.rounded_rectangle([160, 497, 864, 527], radius=15, fill=TEAL + (255,))
d.polygon([(420, 490), (370, 420), (470, 420)], fill=GREEN + (255,))
d.polygon([(600, 534), (550, 604), (650, 604)], fill=CYAN + (255,))

img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", OUT)
