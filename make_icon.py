from PIL import Image, ImageDraw, ImageFont
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# 深色圆角底
d.rounded_rectangle([6, 6, S-6, S-6], radius=52, fill=(15, 17, 23, 255),
                    outline=(122, 162, 255, 255), width=6)
# 顶部青色小点
d.ellipse([28, 30, 52, 54], fill=(94, 234, 212, 255))
# 0/1 字块
def load(sz):
    for name in ("CascadiaMono.ttf", "consola.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, sz)
        except Exception:
            continue
    return ImageFont.load_default()

f = load(96)
d.text((S//2, S//2 - 14), "01", font=f, fill=(230, 235, 245, 255), anchor="mm")
f2 = load(40)
d.text((S//2 + 6, S//2 + 74), "BIN", font=f2, fill=(94, 234, 212, 255), anchor="mm")
img.save("icon.png")
img.save("icon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("icon ok")
