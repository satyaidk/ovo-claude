#!/usr/bin/env python3
"""Draw the step-by-step pictures used by the user manual in README.md.

Everything is drawn from text in this file, or cut from images that are
already in docs/images (the OLED screens come from claude-screens.png and
eyes.gif, which the firmware's own drawing code produced). Run it again after
changing a step, so the pictures and the text stay in sync.

Usage (from the repository root; needs Pillow, a development-only tool):
    python tools/preview/manual_images.py
Output: docs/images/manual/*.png
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs" / "images"
OUT = IMAGES / "manual"

# Colours from the web controller's dark theme, so the manual matches the page.
BG = (17, 30, 58)
PANEL = (27, 43, 77)
INK = (238, 232, 218)
MUTED = (150, 163, 189)
RED = (226, 85, 60)
GREEN = (98, 190, 120)
YELLOW = (238, 196, 84)
BLUE = (110, 168, 240)
TERM_BG = (13, 17, 23)
TERM_INK = (220, 226, 232)
PIXEL = (226, 243, 255)

FONT_DIR = pathlib.Path("/usr/share/fonts/truetype/dejavu")


def font(size, bold=False, mono=False):
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    if bold:
        name += "-Bold"
    try:
        return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


# ---- OLED screens cut from the existing renders ----

CELL_W, CELL_H, GAP = 384, 192, 12
# Order of claude-screens.png, left to right, top to bottom.
SCREENS = ["running", "approval", "task-done", "turn-done", "failed",
           "ready", "stale", "offline", "waiting"]


def oled(name):
    sheet = Image.open(IMAGES / "claude-screens.png").convert("RGB")
    i = SCREENS.index(name)
    x, y = (i % 3) * (CELL_W + GAP), (i // 3) * (CELL_H + GAP)
    return sheet.crop((x, y, x + CELL_W, y + CELL_H))


def eyes_frame(n=0):
    gif = Image.open(IMAGES / "eyes.gif")
    gif.seek(min(n, gif.n_frames - 1))
    return gif.convert("RGB")


def framed(img, pad=14, bezel=(40, 44, 52)):
    """Put an OLED render inside a small dark bezel, like the real module."""
    w, h = img.size
    out = Image.new("RGB", (w + 2 * pad, h + 2 * pad), bezel)
    out.paste(img, (pad, pad))
    return out


# ---- drawing helpers ----

def canvas(w, h, colour=BG):
    img = Image.new("RGB", (w, h), colour)
    return img, ImageDraw.Draw(img)


def box(d, xy, label, sub=None, fill=PANEL, outline=INK):
    d.rounded_rectangle(xy, radius=12, fill=fill, outline=outline, width=2)
    x0, y0, x1, _ = xy
    d.text(((x0 + x1) // 2, y0 + 22), label, fill=INK, font=font(22, bold=True), anchor="mm")
    if sub:
        d.text(((x0 + x1) // 2, y0 + 48), sub, fill=MUTED, font=font(15), anchor="mm")


def pin(d, x, y, label, colour, right=True):
    d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=colour)
    f = font(16, mono=True)
    if right:
        d.text((x - 14, y), label, fill=INK, font=f, anchor="rm")
    else:
        d.text((x + 14, y), label, fill=INK, font=f, anchor="lm")


def wire(d, points, colour):
    d.line(points, fill=colour, width=5, joint="curve")


def arrow(d, x0, y, x1, label=None, colour=INK):
    d.line((x0, y, x1 - 12, y), fill=colour, width=4)
    d.polygon([(x1, y), (x1 - 16, y - 9), (x1 - 16, y + 9)], fill=colour)
    if label:
        d.text(((x0 + x1) // 2, y - 20), label, fill=YELLOW, font=font(17, bold=True), anchor="mm")


def terminal(lines, title="Terminal", width=1000):
    """A terminal window. Each line is a string or a list of (text, colour)."""
    f = font(19, mono=True)
    line_h = 30
    h = 64 + line_h * len(lines) + 20
    img, d = canvas(width, h, TERM_BG)
    d.rectangle((0, 0, width, 40), fill=(36, 41, 51))
    for i, c in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        d.ellipse((16 + i * 24, 13, 30 + i * 24, 27), fill=c)
    d.text((width // 2, 20), title, fill=MUTED, font=font(15), anchor="mm")
    y = 56
    for line in lines:
        parts = [(line, TERM_INK)] if isinstance(line, str) else line
        x = 20
        for text, colour in parts:
            d.text((x, y), text, fill=colour, font=f)
            x += d.textlength(text, font=f)
        y += line_h
    return img


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(path, optimize=True)
    print("wrote", path.relative_to(ROOT))


# ---- the pictures ----

def wiring():
    img, d = canvas(1100, 560)
    d.text((550, 32), "Wiring: three parts to the ESP32-C3", fill=INK, font=font(26, bold=True), anchor="mm")

    # ESP32-C3 SuperMini
    box(d, (380, 110, 720, 500), "ESP32-C3", "SuperMini", fill=(30, 60, 50))
    left = {"GPIO20": 200, "GPIO21": 250, "3V3": 330, "GND": 380}
    right = {"GPIO1": 200, "GPIO2": 330}
    for name, y in left.items():
        pin(d, 380, y, "", INK)
        d.text((396, y), name, fill=INK, font=font(16, mono=True), anchor="lm")
    for name, y in right.items():
        pin(d, 720, y, "", INK)
        d.text((704, y), name, fill=INK, font=font(16, mono=True), anchor="rm")
    d.rounded_rectangle((520, 470, 580, 500), radius=4, fill=(150, 150, 150))
    d.text((550, 456), "USB-C", fill=MUTED, font=font(14), anchor="mm")

    # OLED
    box(d, (40, 110, 260, 420), "OLED", "SSD1306 128x64")
    d.rectangle((70, 170, 230, 250), fill=(0, 0, 0), outline=MUTED)
    d.text((150, 210), "o  o", fill=PIXEL, font=font(30, bold=True), anchor="mm")
    oled_pins = {"SDA": 290, "SCL": 320, "VCC": 350, "GND": 380}
    for name, y in oled_pins.items():
        pin(d, 260, y, name, INK)

    # Touch module
    box(d, (840, 110, 1060, 260), "Touch", "TTP223")
    touch_pins = {"OUT": 160, "VCC": 195, "GND": 230}
    for name, y in touch_pins.items():
        pin(d, 840, y, name, INK, right=False)

    # Buzzer
    box(d, (840, 300, 1060, 450), "Buzzer", "passive")
    buz_pins = {"+": 350, "-": 400}
    for name, y in buz_pins.items():
        pin(d, 840, y, name, INK, right=False)

    # Wires: data in colour, power red, ground grey.
    wire(d, [(266, 290), (320, 290), (320, 200), (374, 200)], BLUE)       # SDA
    wire(d, [(266, 320), (335, 320), (335, 250), (374, 250)], YELLOW)     # SCL
    wire(d, [(266, 350), (350, 350), (350, 330), (374, 330)], RED)        # VCC
    wire(d, [(266, 380), (374, 380)], (160, 160, 160))                    # GND
    wire(d, [(726, 200), (780, 200), (780, 160), (834, 160)], GREEN)      # touch OUT
    wire(d, [(726, 330), (800, 330), (800, 350), (834, 350)], (200, 120, 230))  # buzzer +
    wire(d, [(380, 330), (360, 330), (360, 520), (815, 520), (815, 195), (834, 195)], RED)  # 3V3 to touch
    wire(d, [(380, 380), (368, 380), (368, 535), (825, 535), (825, 230), (834, 230)], (160, 160, 160))
    wire(d, [(825, 400), (834, 400)], (160, 160, 160))                    # buzzer -

    legend = [(BLUE, "SDA  GPIO20"), (YELLOW, "SCL  GPIO21"), (GREEN, "Touch  GPIO1"),
              ((200, 120, 230), "Buzzer  GPIO2"), (RED, "3V3 power"), ((160, 160, 160), "GND")]
    img2, d2 = canvas(1100, 610)
    img2.paste(img, (0, 0))
    for i, (c, t) in enumerate(legend):
        x = 40 + i * 175
        d2.rectangle((x, 578, x + 26, 586), fill=c)
        d2.text((x + 34, 582), t, fill=INK, font=font(15), anchor="lm")
    save(img2, "01-wiring.png")


def arduino_settings():
    img, d = canvas(1000, 500, (245, 245, 245))
    d.rectangle((0, 0, 1000, 44), fill=(0, 129, 132))
    d.text((20, 22), "Arduino IDE 2   File  Edit  Sketch  Tools  Help", fill=(255, 255, 255),
           font=font(18, bold=True), anchor="lm")
    d.text((30, 70), "Tools menu", fill=(40, 40, 40), font=font(20, bold=True))
    rows = [
        ("Board", "ESP32C3 Dev Module", True),
        ("Port", "COM5  (or /dev/ttyACM0)", False),
        ("USB CDC On Boot", "Enabled", True),
        ("CPU Frequency", "160MHz (default)", False),
        ("Flash Size", "4MB (default)", False),
        ("Partition Scheme", "Minimal SPIFFS (1.9MB APP with OTA/128KB SPIFFS)", True),
    ]
    y = 110
    for name, value, must in rows:
        fill = (255, 243, 205) if must else (255, 255, 255)
        d.rectangle((30, y, 970, y + 50), fill=fill, outline=(210, 210, 210))
        d.text((50, y + 25), name, fill=(30, 30, 30), font=font(18, bold=must), anchor="lm")
        d.text((330, y + 25), value, fill=(30, 30, 30), font=font(18), anchor="lm")
        if must:
            d.text((955, y + 25), "must set", fill=(190, 60, 40), font=font(15, bold=True), anchor="rm")
        y += 56
    d.text((30, y + 18), "Yellow rows are required. Without Minimal SPIFFS the upload fails: the firmware is too big.",
           fill=(90, 90, 90), font=font(16))
    save(img, "02-arduino-settings.png")


def secrets():
    lines = [
        [("// firmware/MochiEyes/secrets.h", (120, 140, 160))],
        [("#pragma once", (198, 120, 221))],
        "",
        [("#define ", (198, 120, 221)), ("WIFI_SSID_DEFAULT ", TERM_INK), ('"MyHomeWiFi"', (152, 195, 121)),
         ("     // 2.4 GHz only", (120, 140, 160))],
        [("#define ", (198, 120, 221)), ("WIFI_PASS_DEFAULT ", TERM_INK), ('"my-password"', (152, 195, 121))],
        "",
        [("#define ", (198, 120, 221)), ("AP_SSID ", TERM_INK), ('"Kitty"', (152, 195, 121)),
         ("       // backup hotspot", (120, 140, 160))],
        [("#define ", (198, 120, 221)), ("AP_PASS ", TERM_INK), ('"12345678"', (152, 195, 121)),
         ("    // 8+ characters", (120, 140, 160))],
    ]
    save(terminal(lines, title="secrets.h  (copied from secrets.example.h)"), "03-secrets.png")


def boot():
    eyes = framed(eyes_frame())
    w = eyes.width + 520
    img, d = canvas(w, eyes.height + 40)
    img.paste(eyes, (20, 20))
    x = eyes.width + 50
    d.text((x, 34), "Listen while it boots", fill=INK, font=font(22, bold=True))
    beeps = [("beep", "powered on"), ("beep", "OLED found"), ("high beep", "joined your Wi-Fi")]
    for i, (s, m) in enumerate(beeps):
        y = 80 + i * 40
        d.ellipse((x, y + 4, x + 14, y + 18), fill=GREEN)
        d.text((x + 26, y), f"{s}: {m}", fill=INK, font=font(18))
    d.text((x, 205), "The IP address shows at the bottom", fill=MUTED, font=font(16))
    d.text((x, 228), "of the screen for 8 seconds.", fill=MUTED, font=font(16))
    save(img, "04-boot.png")


def face_cycle():
    eyes = framed(eyes_frame()).resize((300, 165))
    claude = framed(oled("waiting")).resize((300, 165))
    img, d = canvas(1100, 300)
    d.text((550, 30), "Hold the touch pad for 5 seconds to move to the next face",
           fill=INK, font=font(22, bold=True), anchor="mm")
    img.paste(eyes, (30, 80))
    d.rounded_rectangle((400, 80, 700, 245), radius=10, fill=(0, 0, 0), outline=(40, 44, 52), width=14)
    d.text((550, 140), "12:43", fill=PIXEL, font=font(44, bold=True, mono=True), anchor="mm")
    d.text((550, 195), "weather  prices  note", fill=PIXEL, font=font(16, mono=True), anchor="mm")
    img.paste(claude, (770, 80))
    for x0, x1 in ((335, 395), (705, 765)):
        arrow(d, x0, 162, x1, "5 s")
    labels = [(180, "1. Eyes"), (550, "2. Clock"), (920, "3. Claude Code")]
    for x, t in labels:
        d.text((x, 270), t, fill=INK, font=font(18, bold=True), anchor="mm")
    save(img, "05-face-cycle.png")


def send_test():
    term = terminal([
        [("$ ", GREEN), ("python companion/ovo-companion.py send-test \\", TERM_INK)],
        "    --device 192.168.1.62 --state approval --activity \"Run: npm\"",
        [("ok: 192.168.1.62 replied 'ok claude'", GREEN)],
    ], width=820)
    screen = framed(oled("approval"))
    h = max(term.height, screen.height) + 40
    img, d = canvas(term.width + screen.width + 100, h + 30)
    img.paste(term, (20, 20 + (h - 40 - term.height) // 2))
    arrow(d, term.width + 25, 20 + (h - 40) // 2, term.width + 75)
    img.paste(screen, (term.width + 80, 20 + (h - 40 - screen.height) // 2))
    d.text((term.width + 80 + screen.width // 2, h + 8), "two short beeps", fill=YELLOW,
           font=font(17, bold=True), anchor="mm")
    save(img, "06-send-test.png")


def install():
    save(terminal([
        [("$ ", GREEN), ("python companion/ovo-companion.py install --scope user --device 192.168.1.62", TERM_INK)],
        "Claude Code version: 2.1.296",
        [("installed into: ", TERM_INK), ("C:\\Users\\you\\.claude\\settings.json", BLUE)],
        "  transport: http hooks, 14 events: SessionStart, UserPromptSubmit, ...",
        [("  changed:   ", TERM_INK), ("yes", GREEN)],
        "  backup:    C:\\Users\\you\\.claude\\settings.json.ovo-backup-20261013T174503Z",
        "  device:    192.168.1.62 (saved in ...\\ovo-companion\\config.json)",
        [("The companion now starts by itself", GREEN), (": with the next Claude Code session, or after the next", TERM_INK)],
        "reply in a session that is already open. To run it in a terminal instead: ovo-companion run",
    ], width=1180), "07-install.png")


def statusline():
    save(terminal([
        [("$ ", GREEN), ("claude", TERM_INK)],
        "",
        [("> ", YELLOW), ("list the files in this folder and tell me which is the largest", TERM_INK)],
        "",
        [("* ", RED), ("Searching code...", MUTED)],
        "",
        [("OVO ", TERM_INK), ("\u25cf", GREEN), (" | Opus 5.5 | xhigh", TERM_INK),
         ("      <- filled dot: the device is receiving", YELLOW)],
    ], title="Claude Code", width=1000), "08-statusline.png")


def oled_states():
    names = [("ready", "Claude Code is open"), ("running", "Claude is working"),
             ("approval", "Claude needs your OK"), ("task-done", "Finished")]
    tiles = [framed(oled(n)).resize((300, 165)) for n, _ in names]
    img, d = canvas(4 * 300 + 5 * 20, 165 + 80)
    for i, (tile, (_, label)) in enumerate(zip(tiles, names)):
        x = 20 + i * 320
        img.paste(tile, (x, 20))
        d.text((x + 150, 215), f"{i + 1}. {label}", fill=INK, font=font(18, bold=True), anchor="mm")
    save(img, "09-oled-states.png")


def main():
    wiring()
    arduino_settings()
    secrets()
    boot()
    face_cycle()
    send_test()
    install()
    statusline()
    oled_states()


if __name__ == "__main__":
    main()
