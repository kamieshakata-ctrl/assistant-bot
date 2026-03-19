"""
名刺自動生成モジュール
Pillowを使って法人名刺のPNG画像を生成する
"""

import io
import math
import re
import unicodedata
from PIL import Image, ImageDraw, ImageFont

# フォントパス
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_MEDIUM = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"

# 名刺サイズ（91mm x 55mm @ 300dpi）
CARD_W = 1075
CARD_H = 650

# カラーパレット
WHITE = (255, 255, 255)
DARK_BLUE = (15, 40, 90)
MID_BLUE = (30, 80, 160)
LIGHT_BLUE = (80, 140, 220)
CRYSTAL_BLUE = (100, 180, 255)
PALE_BLUE = (200, 225, 255)
GRAY = (100, 100, 110)
LIGHT_GRAY = (230, 235, 245)


def draw_crystal_decoration(draw: ImageDraw.Draw, x: int, y: int, size: int = 120):
    """右上に青系クリスタル多角形装飾を描画する"""
    import random
    random.seed(42)

    # クリスタルの多角形群を描画
    polygons = [
        # 大きな三角形群
        [(x, y), (x - size * 0.6, y + size * 0.4), (x - size * 0.2, y + size * 0.7)],
        [(x, y), (x - size * 0.2, y + size * 0.7), (x + size * 0.1, y + size * 0.5)],
        [(x, y), (x - size * 0.6, y + size * 0.4), (x - size * 0.9, y + size * 0.1)],
        [(x - size * 0.6, y + size * 0.4), (x - size * 0.9, y + size * 0.1), (x - size * 0.8, y + size * 0.6)],
        [(x - size * 0.2, y + size * 0.7), (x - size * 0.6, y + size * 0.4), (x - size * 0.8, y + size * 0.6)],
        [(x - size * 0.2, y + size * 0.7), (x - size * 0.8, y + size * 0.6), (x - size * 0.5, y + size * 0.9)],
        [(x + size * 0.1, y + size * 0.5), (x - size * 0.2, y + size * 0.7), (x, y + size * 0.9)],
        [(x - size * 0.9, y + size * 0.1), (x - size * 1.1, y + size * 0.3), (x - size * 0.8, y + size * 0.6)],
    ]

    colors = [
        (15, 60, 140, 200),
        (30, 90, 180, 180),
        (50, 120, 200, 160),
        (80, 150, 220, 140),
        (20, 70, 160, 190),
        (40, 100, 190, 170),
        (70, 140, 210, 150),
        (10, 50, 130, 210),
    ]

    for poly, color in zip(polygons, colors):
        pts = [(int(px), int(py)) for px, py in poly]
        draw.polygon(pts, fill=color[:3])

    # ハイライト（明るい線）
    for poly in polygons[:4]:
        pts = [(int(px), int(py)) for px, py in poly]
        draw.polygon(pts, outline=(180, 210, 255), width=1)


def generate_email(hojin_name: str) -> str:
    """法人名から英語短縮形のメールアドレスを生成する"""
    # 「一般社団法人」「株式会社」などのプレフィックスを除去
    name = hojin_name
    for prefix in ["一般社団法人", "株式会社", "有限会社", "合同会社", "特定非営利活動法人", "NPO法人"]:
        name = name.replace(prefix, "")
    name = name.strip()

    # 英数字のみ抽出（全角英数字を半角に変換）
    normalized = unicodedata.normalize("NFKC", name)

    # 英字のみ取り出す
    english_chars = re.findall(r'[a-zA-Z0-9]', normalized)
    if english_chars:
        base = "".join(english_chars).lower()[:12]
    else:
        # ひらがな・カタカナをローマ字変換（簡易）
        kana_map = {
            'ア': 'a', 'イ': 'i', 'ウ': 'u', 'エ': 'e', 'オ': 'o',
            'カ': 'ka', 'キ': 'ki', 'ク': 'ku', 'ケ': 'ke', 'コ': 'ko',
            'サ': 'sa', 'シ': 'si', 'ス': 'su', 'セ': 'se', 'ソ': 'so',
            'タ': 'ta', 'チ': 'ti', 'ツ': 'tu', 'テ': 'te', 'ト': 'to',
            'ナ': 'na', 'ニ': 'ni', 'ヌ': 'nu', 'ネ': 'ne', 'ノ': 'no',
            'ハ': 'ha', 'ヒ': 'hi', 'フ': 'fu', 'ヘ': 'he', 'ホ': 'ho',
            'マ': 'ma', 'ミ': 'mi', 'ム': 'mu', 'メ': 'me', 'モ': 'mo',
            'ヤ': 'ya', 'ユ': 'yu', 'ヨ': 'yo',
            'ラ': 'ra', 'リ': 'ri', 'ル': 'ru', 'レ': 're', 'ロ': 'ro',
            'ワ': 'wa', 'ヲ': 'wo', 'ン': 'n',
            'ガ': 'ga', 'ギ': 'gi', 'グ': 'gu', 'ゲ': 'ge', 'ゴ': 'go',
            'ザ': 'za', 'ジ': 'ji', 'ズ': 'zu', 'ゼ': 'ze', 'ゾ': 'zo',
            'ダ': 'da', 'ヂ': 'di', 'ヅ': 'du', 'デ': 'de', 'ド': 'do',
            'バ': 'ba', 'ビ': 'bi', 'ブ': 'bu', 'ベ': 'be', 'ボ': 'bo',
            'パ': 'pa', 'ピ': 'pi', 'プ': 'pu', 'ペ': 'pe', 'ポ': 'po',
            'ァ': 'a', 'ィ': 'i', 'ゥ': 'u', 'ェ': 'e', 'ォ': 'o',
            'ッ': 't', 'ャ': 'ya', 'ュ': 'yu', 'ョ': 'yo',
            'ー': '',
            'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
            'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
            'さ': 'sa', 'し': 'si', 'す': 'su', 'せ': 'se', 'そ': 'so',
            'た': 'ta', 'ち': 'ti', 'つ': 'tu', 'て': 'te', 'と': 'to',
            'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
            'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
            'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
            'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
            'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
            'わ': 'wa', 'を': 'wo', 'ん': 'n',
        }
        base = ""
        for ch in normalized:
            base += kana_map.get(ch, "")
        base = base[:12] if base else "info"

    # 数字を末尾に付加（法人名の特徴から）
    # 法人名の文字数から数字を生成
    num = (len(hojin_name) * 7 + 13) % 90 + 10
    return f"{base}{num}@gmail.com"


def split_hojin_name(hojin_name: str):
    """
    法人名を2行に分割する。
    「一般社団法人」で改行して法人名が2行にならないようにする。
    """
    prefixes = ["一般社団法人", "株式会社", "有限会社", "合同会社", "特定非営利活動法人", "NPO法人"]
    for prefix in prefixes:
        if hojin_name.startswith(prefix):
            rest = hojin_name[len(prefix):]
            return prefix, rest
    return None, hojin_name


def create_business_card(
    hojin_name: str,
    tel: str = "",
    address: str = "",
    tantousha: str = "",
) -> bytes:
    """
    名刺PNG画像を生成してbytesで返す
    """
    # キャンバス作成（白背景）
    img = Image.new("RGB", (CARD_W, CARD_H), WHITE)
    draw = ImageDraw.Draw(img, "RGBA")

    # ── 背景装飾 ──────────────────────────────────────────
    # 左側に薄い青のアクセントライン
    draw.rectangle([0, 0, 8, CARD_H], fill=DARK_BLUE)
    draw.rectangle([12, 0, 16, CARD_H], fill=LIGHT_BLUE)

    # 下部に薄いグラデーション風ライン
    draw.rectangle([0, CARD_H - 6, CARD_W, CARD_H], fill=DARK_BLUE)
    draw.rectangle([0, CARD_H - 10, CARD_W, CARD_H - 6], fill=LIGHT_BLUE)

    # ── 右上クリスタル装飾 ────────────────────────────────
    draw_crystal_decoration(draw, CARD_W - 10, 10, size=160)

    # ── フォント読み込み ──────────────────────────────────
    try:
        font_hojin_prefix = ImageFont.truetype(FONT_REGULAR, 28)
        font_hojin_name = ImageFont.truetype(FONT_BOLD, 42)
        font_name = ImageFont.truetype(FONT_BOLD, 36)
        font_label = ImageFont.truetype(FONT_REGULAR, 22)
        font_value = ImageFont.truetype(FONT_REGULAR, 24)
        font_email = ImageFont.truetype(FONT_REGULAR, 22)
    except Exception:
        font_hojin_prefix = ImageFont.load_default()
        font_hojin_name = ImageFont.load_default()
        font_name = ImageFont.load_default()
        font_label = ImageFont.load_default()
        font_value = ImageFont.load_default()
        font_email = ImageFont.load_default()

    # ── 法人名（2行表示）────────────────────────────────
    prefix, name_rest = split_hojin_name(hojin_name)

    x_start = 40
    y_hojin = 60

    if prefix:
        draw.text((x_start, y_hojin), prefix, font=font_hojin_prefix, fill=GRAY)
        y_name = y_hojin + 36
    else:
        y_name = y_hojin

    draw.text((x_start, y_name), name_rest, font=font_hojin_name, fill=DARK_BLUE)

    # ── 担当者名 ──────────────────────────────────────────
    y_tantou = y_name + 70
    if tantousha:
        draw.text((x_start, y_tantou), tantousha, font=font_name, fill=DARK_BLUE)
        y_info_start = y_tantou + 60
    else:
        y_info_start = y_name + 60

    # ── 区切り線 ──────────────────────────────────────────
    draw.rectangle([x_start, y_info_start, x_start + 400, y_info_start + 2], fill=LIGHT_BLUE)
    y_info_start += 16

    # ── 連絡先情報 ────────────────────────────────────────
    email = generate_email(hojin_name)
    info_items = []

    if tel:
        info_items.append(("TEL", tel))
    if address:
        info_items.append(("住所", address))
    info_items.append(("MAIL", email))

    for label, value in info_items:
        # ラベル
        draw.text((x_start, y_info_start), f"{label}：", font=font_label, fill=LIGHT_BLUE)
        # 値
        draw.text((x_start + 80, y_info_start), value, font=font_value, fill=DARK_BLUE)
        y_info_start += 34

    # ── 出力 ──────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(300, 300))
    buf.seek(0)
    return buf.read()


if __name__ == "__main__":
    # テスト生成
    data = create_business_card(
        hojin_name="一般社団法人アートフォーラムNOAN",
        tel="050-1726-6522",
        address="東京都世田谷区上馬1-2-3",
        tantousha="秦 明翔",
    )
    with open("/tmp/test_meishi.png", "wb") as f:
        f.write(data)
    print("Generated: /tmp/test_meishi.png")
    print("Email:", generate_email("一般社団法人アートフォーラムNOAN"))
