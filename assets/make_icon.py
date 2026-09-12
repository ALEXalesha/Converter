import os

from PIL import Image, ImageDraw

BLUE = (37, 99, 235)
S = 256

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, 248, 248), radius=48, fill=BLUE)
d.rounded_rectangle((66, 30, 190, 226), radius=10, fill="white")
x0, y0, x1, y1 = 82, 46, 174, 166
d.rectangle((x0, y0, x1, y1), outline=BLUE, width=6)
for x in (105, 128, 151):
    d.line((x, y0, x, y1), fill=BLUE, width=4)
for y in (76, 106, 136):
    d.line((x0, y, x1, y), fill=(147, 197, 253), width=4)
for x in range(76, 184, 16):
    d.line((x, 186, x + 8, 186), fill=(148, 163, 184), width=4)

img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
