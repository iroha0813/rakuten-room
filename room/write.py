"""Claude API による商品プレゼン文の生成。

1商品1リクエスト。1日5件なら Batch API のオーバーヘッドの方が大きい。
将来20件超に増やすなら Batch API（50%オフ）に切り替える。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from datetime import date, timedelta
from typing import Any, Iterable

from . import config, store

# 絶対額。これが古くなると「価格が違う」という一番きつい嘘になるので常に除外する。
YEN_PATTERN = re.compile(
    r"[\d０-９][\d０-９,，.．]*\s*円"        # 1,980円 / １９８０円
    r"|[¥￥]\s*[\d０-９]",                   # ¥1980
    re.IGNORECASE,
)

# お得情報。変動はするが「条件が変わった」程度で、絶対額ほど致命的ではない。
# settings.yaml の llm.allow_deal_info で許可/禁止を切り替える。
DEAL_PATTERN = re.compile(
    r"[\d０-９]+\s*[%％]\s*(?:OFF|オフ)"     # 30%OFF
    r"|[\d０-９][\d０-９,，]*\s*ポイント"     # 500ポイント
    r"|[\d０-９]+\s*倍"                      # P10倍
    r"|クーポン|送料無料|半額|セール|タイムセール",
    re.IGNORECASE,
)


# 期間が書かれていないので検証しようがないお得情報。
# 「マラソン限定」だけでは開催中か分からず、書くと嘘になりうる。
UNVERIFIABLE_DEAL = re.compile(
    r"マラソン|スーパーSALE|スーパーセール|タイムセール|期間限定|本日限り|今だけ|ラストチャンス"
)

# お得情報として扱う表現すべて。伏せ字と検出の両方でこれを使う。
ANY_DEAL_PATTERN = re.compile(
    f"{DEAL_PATTERN.pattern}|{UNVERIFIABLE_DEAL.pattern}", re.IGNORECASE
)


def contains_price(text: str, allow_deal_info: bool = False) -> bool:
    """本文に書いてはいけない金額表現が混ざっていないか。

    allow_deal_info が True なら、クーポンや送料無料といったお得情報は許すが、
    絶対額だけは常に弾く。
    """
    text = text or ""
    if YEN_PATTERN.search(text):
        return True
    if not allow_deal_info and ANY_DEAL_PATTERN.search(text):
        return True
    return False


# 1行目がこれらで終わっていたら、文の途中で改行している。
# フィードでは冒頭しか見えないので、途中で切れるとフックにならない。
DANGLING_TAILS = ("、", "，", "…", "・", "が", "けど", "ので", "から")

# フィードで見えるのは40〜60字。1行目がこれを超えるとフックが薄まる。
FIRST_LINE_LIMIT = 50

# 1行目に置くと弱くなる表現（社会的証明は根拠であってフックではない）。
REVIEW_MENTION = re.compile(r"レビュー|口コミ|[\d０-９][\d０-９,，]*\s*件|星[\d０-９]")

# 絵文字と装飾記号。ROOMのフィードでは装飾のない説明文は読み飛ばされる。
EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001faff"   # 絵文字全般
    "\U0001f000-\U0001f2ff"   # 記号系絵文字
    "☀-➿"           # ☀ ✨ ❤ など
    "⬀-⯿"           # ⭐ など
    "←-⇿"           # 矢印
    "〰〽‼⁉"
    "♡♥◎✓☑※‼]"
)
MIN_EMOJI = 2
MAX_EMOJI = 8


def count_emoji(text: str) -> int:
    return len(EMOJI_PATTERN.findall(text or ""))


# ハッシュタグは型ごとに個数を変える。毎回3個並ぶと投稿の末尾が同じ形になる。
HASHTAG_PATTERN = re.compile(r"[#＃][^\s#＃]+")


def count_hashtags(text: str) -> int:
    return len(HASHTAG_PATTERN.findall(text or ""))


# 裏づけなしに書かれると嘘になる実績・最上級表現。
# 「2024年の年間1位と上半期1位」を「2年連続1位」と書いてしまう事故が実際に起きた。
CLAIM_PATTERN = re.compile(
    r"\d+\s*年連続|No\.?\s*1|ナンバーワン|第\s*\d+\s*位|\d+\s*冠"
    r"|ランキング\s*\d+\s*位|受賞|認定"
    r"|業界(?:初|一|最)|世界一|日本一|最強|最高峰|唯一",
    re.IGNORECASE,
)


def _normalize_claim(text: str) -> str:
    return re.sub(r"[\s　・.．]", "", text).lower()


def ungrounded_claims(text: str, source: str) -> list[str]:
    """商品情報に見当たらない実績・最上級表現を拾う。"""
    haystack = _normalize_claim(source)
    found: list[str] = []
    for match in CLAIM_PATTERN.finditer(text or ""):
        claim = match.group(0)
        if _normalize_claim(claim) not in haystack and claim not in found:
            found.append(claim)
    return found


def review_pitch(
    text: str,
    max_chars: int,
    allow_deal_info: bool = False,
    source: str = "",
    hashtags: int | None = None,
) -> list[str]:
    """生成文の問題点を並べる。空なら合格。

    hashtags を渡すと、その型で決めた個数に収まっているかも見る。
    """
    problems: list[str] = []

    if source:
        claims = ungrounded_claims(text, source)
        if claims:
            problems.append(
                f"商品情報に書かれていない実績表現があります: {'、'.join(claims)}。"
                "商品情報にそのまま書かれている実績だけを、書かれているとおりに使ってください。"
                "複数の実績を組み合わせて新しい実績を作らないでください。"
            )
    if contains_price(text, allow_deal_info):
        if allow_deal_info:
            problems.append(
                "「1,980円」のような具体的な金額が含まれています。ROOMが価格を自動表示するうえ、"
                "価格が変わると本文だけが嘘になります。クーポンや送料無料には触れて構いませんが、"
                "金額そのものは書かないでください。"
            )
        else:
            problems.append(
                "金額・価格・割引率が含まれています。ROOMが価格を自動表示するため本文には不要で、"
                "価格改定で古い情報になります。金額に一切触れないでください。"
            )
    if len(text) > max_chars:
        problems.append(f"{max_chars}字以内のところ{len(text)}字あります。削ってください。")

    first_line = text.split("\n")[0].strip()
    if first_line.endswith(DANGLING_TAILS):
        problems.append(
            f"1行目「{first_line}」が文の途中で切れています。"
            "フィードでは冒頭しか表示されないので、1行目はそれだけで意味の通る"
            "完結した一文にしてください。文の途中で改行しないでください。"
        )
    if len(first_line) > FIRST_LINE_LIMIT:
        problems.append(
            f"1行目が{len(first_line)}字あります。フィードで見えるのは冒頭40〜60字なので、"
            f"1行目は{FIRST_LINE_LIMIT}字以内の一文にしてください。"
        )
    if REVIEW_MENTION.search(first_line):
        problems.append(
            "1行目にレビュー件数や評価が入っています。それは読み手を止める力が弱いので、"
            "中盤に回してください。1行目は困りごとや使う場面から始めてください。"
        )

    emoji = count_emoji(text)
    if emoji < MIN_EMOJI:
        problems.append(
            f"絵文字が{emoji}個しかありません。ROOMのフィードでは装飾のない説明文は"
            f"読み飛ばされます。感情の乗るところに{MIN_EMOJI}〜{MAX_EMOJI}個入れてください。"
        )
    elif emoji > MAX_EMOJI:
        problems.append(
            f"絵文字が{emoji}個あります。多すぎると読みにくいので{MAX_EMOJI}個までに"
            "減らしてください。"
        )

    if hashtags is not None:
        found = count_hashtags(text)
        if found > hashtags:
            problems.append(
                f"ハッシュタグが{found}個あります。この型では{hashtags}個までです。"
                "多いほうから削ってください。"
            )
        elif found == 0:
            problems.append(f"ハッシュタグがありません。末尾に{hashtags}個まで付けてください。")
    return problems


# 商品名によく現れる開催期間の書き方。
_PERIOD_RANGE = re.compile(r"(\d{1,2})/(\d{1,2})\s*[～〜~\-−–]\s*(?:(\d{1,2})/)?(\d{1,2})")
_PERIOD_DEADLINE = re.compile(r"(?:(\d{1,2})/)?(\d{1,2})\s*日?\s*(?:正午|\d{1,2}:\d{2})?\s*まで")


def deal_period_status(text: str, today: date) -> str:
    """商品名に書かれたセール期間が今日を含むか。

    "active"       今日が期間内、または期間の記載がなく検証も不要
    "outside"      今日が期間外（まだ始まっていない／もう終わった）
    "unverifiable" 「マラソン限定」など期間が書かれておらず確認できない
    """
    text = text or ""

    match = _PERIOD_RANGE.search(text)
    if match:
        start_month, start_day, end_month, end_day = match.groups()
        start = _as_date(int(start_month), int(start_day), today)
        end = _as_date(int(end_month or start_month), int(end_day), today)
        if start and end:
            return "active" if start <= today <= end else "outside"

    match = _PERIOD_DEADLINE.search(text)
    if match:
        month, day = match.groups()
        deadline = _as_date(int(month) if month else today.month, int(day), today)
        if deadline:
            # 「27日正午まで」は当日中に切れる。当日は期限切れ扱いにして安全側に倒す。
            return "active" if today < deadline else "outside"

    if UNVERIFIABLE_DEAL.search(text):
        return "unverifiable"
    return "active"


def _as_date(month: int, day: int, today: date) -> date | None:
    """月日だけの表記に年を補う。年末年始をまたぐ場合は翌年とみなす。"""
    for year in (today.year, today.year + 1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def strip_prices(text: str, allow_deal_info: bool = False) -> str:
    """書かせたくない金額表現を伏せる。

    楽天の商品名・商品説明には「＼81%OFF＆P2倍で2,174円！／」のような
    金額が頻繁に含まれる。そのまま渡すとモデルが書き写してしまうので、
    プロンプトに入れる前に落とす。
    allow_deal_info が True なら、クーポンや割引率は残して絶対額だけ伏せる。
    """
    masked = YEN_PATTERN.sub("〈金額〉", text or "")
    if not allow_deal_info:
        masked = ANY_DEAL_PATTERN.sub("〈お得情報〉", masked)
    return masked


SYSTEM_PROMPT = """あなたは楽天ROOMで商品を紹介する日本語のライターです。
与えられた商品情報だけをもとに、ROOMのコメント欄に載せる紹介文を1本書いてください。

■ 最重要
ROOMのフィードでは、この文章の最初の2〜3行（およそ40〜60字）しか表示されません。
残りは「続きを読む」の中に隠れ、ハッシュタグは誰の目にも触れません。
1行目で読み手の指を止められなければ、後ろは読まれません。
結論・共感・意外性のいずれかを1行目に置いてください。説明から入らないこと。

■ 文体（ここを外すとフィードで埋もれます）
ROOMの投稿は、友達に「これ良かったよ」と教える調子で書かれています。
実際に反応が集まっている投稿はこういう温度感です。

  可愛いー！！うさぎの耳が離れてるけども🥺
  美味しすぎて手が止まりません😊💕
  あると、ほんっとうに便利！！！🍕

商品カタログのような説明文は、この中に置かれると読み飛ばされます。
- 絵文字を2〜5個使う。文末や区切りに置き、感情の乗るところに添える。
  並べ立てず、意味のある位置に置くこと。
- 「〜ですよ」「〜なんです」「〜しちゃいます」のような話し言葉を使う。
  「〜します」「〜できます」だけで固めない。
- ◎ ✨ ♡ ‼ のような記号で区切ってもよい。
- ただし、誇張や事実の飾りつけに使わない。装飾は温度であって内容ではありません。

■ 書かないこと
{deal_rule}
- 商品名をそのまま書き写さない。ROOMが商品名も表示する。
- 実際に自分が使った体験を創作しない。「届いてすぐ使ってます」のような
  未確認の体験談は書かない。商品の特徴やレビューの傾向として伝える。
- 効果・効能の断定（「必ず痩せる」「絶対に壊れない」など）はしない。
- 「No.1」「◯年連続1位」「業界唯一」のような実績は、商品情報にそのとおり
  書かれている場合だけ、書かれているとおりに使う。複数の実績を足し合わせて
  新しい実績を作らない。
- 商品情報から読み取れない仕様を推測して書かない。たとえば「骨取り」の商品を
  「骨が小さい」と書き換えるような言い換えは、商品を誤解させるのでしない。
- 訳あり品・規格外品など買う人が知っておくべき条件が商品情報にある場合は、
  隠さずに触れる。

■ 1行目（どの型で書くときも共通）
- 50字以内の完結した一文。読点で終わらせない。
- レビュー件数・評価・星の数を1行目に書かない。読み手を止める力が弱い。
- 文の途中で改行しない。改行は文の切れ目にだけ入れる。

■ 全体に共通する体裁
- 全体で{max_chars}字以内。日本語。
- レビュー件数や評価に触れるのは多くても1回だけ。数字を並べず、
  「なぜ支持され続けているのか」の裏づけとして自然に織り込む。
- 改行で読みやすく区切る。スマホの画面幅を意識する。

構成・分量・ハッシュタグの数は商品ごとに「指定された型」で変わります。
型は毎回違うものが渡されるので、前に書いたものと同じ運びにしないでください。

出力は紹介文の本文のみ。前置き・見出し・囲みの記号は付けない。"""

# 金額の扱い。settings.yaml の llm.allow_deal_info で切り替える。
DEAL_RULE_STRICT = """- 金額・価格・割引率・ポイント倍率・クーポンを書かない。ROOMが商品価格を
  自動表示するため重複であり、セールで変動するので本文だけが古くなる。"""

DEAL_RULE_ALLOWED = """- 「1,980円」のような具体的な金額は書かない。ROOMが価格を自動表示するうえ、
  価格が変わると本文だけが嘘になる。
- クーポン・送料無料・ポイント倍率・セールといったお得情報は書いてよい。
  ROOMで最も反応が取れる訴求なので、該当するなら1行目に置いてよい。
  ただし商品情報に書かれていないお得情報をでっち上げてはいけない。
  クーポンやセールの記載が商品情報になければ、お得情報には触れないこと。"""

# 同じ構成の繰り返しを避けるため、商品ごとに切り口を変える。
# フィードで最初に見えるのは40〜60字なので、どれも「1行目で言い切れる」角度にする。
ANGLES = [
    "どんな困りごとが解決するのかを最初に言い切る",
    "使う場面をひとつ具体的に想像させる",
    "贈り物として渡したときに喜ばれる理由",
    "仕様の中で一番効く一点だけに絞る",
    "定番として選ばれ続けている理由",
    "見落とされがちな利点や、意外な使い道",
]

# お得情報を許可しているときだけ回す切り口。ROOMで最も反応が取れる型。
DEAL_ANGLE = "商品情報にあるお得情報（クーポン・送料無料・ポイント）を最初に出す"

# 投稿の「型」。切り口（何を言うか）とは独立に、構成そのものを差し替える。
# 切り口だけ変えても骨格が1種類だとフィードでは同じ投稿に見えるため、
# 構成・分量・ハッシュタグ数をまとめて振る。
# target_chars は目安で、上限は settings.yaml の llm.max_chars が握る。
FORMATS: list[dict[str, Any]] = [
    {
        "key": "hook_reason",
        "label": "フック→根拠",
        "hashtags": 3,
        "target_chars": 150,
        "structure": (
            "1行目でフックを置き、続けてその根拠を具体的に述べ、最後にハッシュタグ。\n"
            "根拠は切り口を軸に据えて、1つか2つに絞る。"
        ),
    },
    {
        "key": "question",
        "label": "問いかけ→答え",
        "hashtags": 2,
        "target_chars": 130,
        "structure": (
            "1行目を読み手への問いかけにして、次の行からその答えを返す。\n"
            "問いは「〜ありませんか？」のような、読み手が自分ごとにできるものにする。\n"
            "答えは長く説明せず、2〜3行で言い切る。"
        ),
    },
    {
        "key": "scene",
        "label": "場面のスケッチ",
        "hashtags": 2,
        "target_chars": 140,
        "structure": (
            "使っている場面を短い行で重ねて描く。説明ではなく情景で見せる。\n"
            "1行を短く保ち、改行を多めに入れてリズムを作る。\n"
            "最後の1〜2行だけ、その場面が成り立つ理由に触れる。"
        ),
    },
    {
        "key": "bullet",
        "label": "箇条書き",
        "hashtags": 3,
        "target_chars": 160,
        "structure": (
            "1行目にフックを置き、そのあと「・」で始まる短い箇条書きを3つ並べる。\n"
            "各項目は20字前後。長い文を箇条書きの形に切っただけにしない。\n"
            "箇条書きのあとに一言添えてからハッシュタグ。"
        ),
    },
    {
        "key": "oneline",
        "label": "短文で言い切る",
        "hashtags": 1,
        "target_chars": 80,
        "structure": (
            "全体を2〜3行に収める。説明を足さず、一番効く一点だけを言い切る。\n"
            "根拠を並べたくなっても書かない。短さそのものが目を引く型。\n"
            "ハッシュタグは1個だけ。"
        ),
    },
    {
        "key": "honest",
        "label": "本音から入る",
        "hashtags": 2,
        "target_chars": 150,
        "structure": (
            "「正直なところ」「最初は迷ったんですが」のように、留保や本音から入る。\n"
            "そのうえで、それでも良いと言える理由に着地させる。\n"
            "商品情報に訳あり・規格外などの条件があれば、この型ではそこに正面から触れる。"
        ),
    },
]


def _recent_keys(history: Iterable[dict[str, Any]], key: str, days: int, today: date) -> set[str]:
    """直近 days 日で使った切り口／型。使い回しを後回しにするために見る。"""
    cutoff = today - timedelta(days=days)
    used: set[str] = set()
    for record in history or []:
        try:
            presented = date.fromisoformat(str(record.get("presentedAt", "")))
        except ValueError:
            continue
        if presented < cutoff:
            continue
        value = record.get(key)
        if value:
            used.add(str(value))
    return used


def _rotate(options: list[Any], recent: set[str], count: int, rng: random.Random, key: str) -> list[Any]:
    """直近で使ったものを後回しにしつつ、重複なく count 個を選ぶ。

    options を使い切る場合だけ先頭から回り込む（1日の件数が選択肢より多いとき）。
    """
    fresh = [o for o in options if str(o if isinstance(o, str) else o[key]) not in recent]
    stale = [o for o in options if str(o if isinstance(o, str) else o[key]) in recent]
    rng.shuffle(fresh)
    rng.shuffle(stale)
    ordered = fresh + stale
    return [ordered[i % len(ordered)] for i in range(count)]


def plan_variations(
    count: int,
    *,
    allow_deal_info: bool = False,
    history: Iterable[dict[str, Any]] | None = None,
    day: date | None = None,
    window_days: int = 14,
) -> list[tuple[str, dict[str, Any]]]:
    """その日の (切り口, 型) の組み合わせを決める。

    以前は順位で切り口を決めていたため、3番目が毎日「贈り物」枠になり、
    末尾の切り口は一度も使われなかった。日付を種にして振り直すことで、
    同じ商品が何番目に来ても違う型で紹介される。

    日付を種にしているので、同じ日に再実行すれば同じ組み合わせが出る。
    """
    day = day or date.today()
    history = list(history or [])
    angles = ANGLES + [DEAL_ANGLE] if allow_deal_info else list(ANGLES)

    rng = random.Random(day.toordinal())
    chosen_angles = _rotate(
        angles, _recent_keys(history, "angle", window_days, day), count, rng, ""
    )
    chosen_formats = _rotate(
        FORMATS, _recent_keys(history, "format", window_days, day), count, rng, "key"
    )
    return list(zip(chosen_angles, chosen_formats))

# 価格は「1万円未満/1万円台/…」のような粒度でも本文に漏れると陳腐化するため、
# モデルには数値を一切渡さず、質感だけを伝える。
PRICE_BANDS: list[tuple[int, str]] = [
    (3000, "気軽に試せる価格帯"),
    (10000, "日用品としては少し奮発する価格帯"),
    (30000, "じっくり選んで買う価格帯"),
]
PRICE_BAND_TOP = "しっかりした買い物になる価格帯"


def price_band(price: int) -> str:
    """価格の質感だけを表すラベル。金額そのものはモデルに渡さない。"""
    for threshold, label in PRICE_BANDS:
        if price < threshold:
            return label
    return PRICE_BAND_TOP


def build_prompt(
    item: dict[str, Any],
    genre_label: str,
    angle: str,
    allow_deal_info: bool = False,
    today: date | None = None,
    fmt: dict[str, Any] | None = None,
) -> str:
    # 期間外・期間不明のセールは、書かれたら嘘になるのでモデルに見せない。
    # 「9/1〜9/13のクーポン」を8月に紹介する、といった事故を入口で防ぐ。
    if allow_deal_info:
        status = deal_period_status(item.get("itemName") or "", today or date.today())
        allow_deal_info = status == "active"
        item["_deal_status"] = status

    # 商品名・キャッチコピー・商品説明には金額が埋まっていることが多いので伏せる。
    def clean(value: str | None) -> str | None:
        return strip_prices(value or "", allow_deal_info) or None

    facts = {
        "商品名": clean(item.get("itemName")),
        # 金額は渡さない。渡すと本文に書かれ、価格改定で古い情報になる。
        "価格の質感": price_band(int(item.get("itemPrice") or 0)),
        "ジャンル": genre_label,
        "ショップ名": item.get("shopName"),
        "レビュー平均": item.get("reviewAverage"),
        "レビュー件数": item.get("reviewCount"),
        "キャッチコピー": clean(item.get("catchcopy")),
        "商品説明": clean((item.get("itemCaption") or "")[:400]),
    }
    lines = [f"- {k}: {v}" for k, v in facts.items() if v not in (None, "")]
    prompt = "商品情報:\n" + "\n".join(lines) + f"\n\n指定された切り口: {angle}"

    fmt = fmt or FORMATS[0]
    prompt += (
        f"\n\n指定された型: {fmt['label']}\n"
        f"{fmt['structure']}\n"
        f"ハッシュタグは{fmt['hashtags']}個まで。全体は{fmt['target_chars']}字前後を目安にする。"
    )
    return prompt


def _client():
    config.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise config.SetupError(
            "ANTHROPIC_API_KEY が未設定です。\n"
            f"プロジェクト直下の .env に記入してください（テンプレート: .env.example）。\n"
            f"  {config.DOTENV_PATH}\n"
            "キーは console.anthropic.com → Settings → API keys で発行できます。"
        )
    from anthropic import Anthropic

    return Anthropic()


def _say(message: str) -> None:
    """進捗の出力で本処理を落とさない。

    room/__init__.py で標準出力をUTF-8にしているが、出力先を差し替えられた
    場合などに備えて二重に守る。生成結果より警告の表示を優先しない。
    """
    try:
        print(message)
    except Exception:  # noqa: BLE001 - 表示の失敗で生成を捨てない
        pass


def _ask(client, system: str, messages: list[dict[str, Any]], llm_cfg: dict[str, Any]) -> str:
    response = client.messages.create(
        model=str(llm_cfg.get("model", "claude-haiku-4-5")),
        max_tokens=int(llm_cfg.get("max_tokens", 1024)),
        system=system,
        messages=messages,
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


def generate(
    items: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    dry_run: bool = False,
    history: Iterable[dict[str, Any]] | None = None,
    day: date | None = None,
) -> list[dict[str, Any]]:
    """各商品に `_pitch` を付与する。1件の失敗で全体を落とさない。"""
    llm_cfg = settings.get("llm", {})
    max_chars = int(llm_cfg.get("max_chars", 200))
    allow_deals = bool(llm_cfg.get("allow_deal_info", False))
    system = SYSTEM_PROMPT.format(
        max_chars=max_chars,
        deal_rule=DEAL_RULE_ALLOWED if allow_deals else DEAL_RULE_STRICT,
    )
    genres = settings["genres"]
    variations = plan_variations(
        len(items),
        allow_deal_info=allow_deals,
        history=history,
        day=day,
        window_days=int(llm_cfg.get("variation_window_days", 14)),
    )

    client = None if dry_run else _client()

    for index, item in enumerate(items):
        genre_label = genres.get(item.get("_genre_key"), {}).get("label", "")
        angle, fmt = variations[index]
        prompt = build_prompt(item, genre_label, angle, allow_deals, today=day, fmt=fmt)
        # build_prompt が期間を見て落としている場合があるので、実際の可否を使う
        item_allows_deals = allow_deals and item.get("_deal_status", "active") == "active"
        # 実績表現の裏づけ元。商品名・キャッチ・説明のどこかに書かれていればよい。
        source = " ".join(
            str(item.get(key) or "")
            for key in ("itemName", "catchcopy", "itemCaption")
        )
        item["_angle"] = angle
        item["_format"] = fmt["key"]

        if dry_run:
            print("=" * 70)
            print(f"[{index + 1}] {item.get('itemName')}")
            print("-" * 70)
            print(prompt)
            item["_pitch"] = None
            continue

        try:
            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            text = _ask(client, system, messages, llm_cfg)

            # 得られた時点で確定させる。以降の手直しで例外が出ても結果を失わない。
            item["_pitch"] = text or None

            # 制約違反は一度だけ指摘して直させる。2回目も駄目なら初回の文を残す。
            problems = (
                review_pitch(
                    text, max_chars, item_allows_deals, source, fmt["hashtags"]
                )
                if text
                else []
            )
            if problems:
                name = str(item.get("itemName", ""))[:24]
                _say(f"[warn] {name}: 書き直します / {' / '.join(problems)}")
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": "\n".join(
                            ["以下を直して書き直してください。"]
                            + [f"- {p}" for p in problems]
                            + ["本文のみを出力してください。"]
                        ),
                    },
                ]
                retried = _ask(client, system, messages, llm_cfg)
                remaining = (
                    review_pitch(
                        retried, max_chars, item_allows_deals, source, fmt["hashtags"]
                    )
                    if retried
                    else problems
                )
                # 完璧でなくても指摘が減ったなら採用する。全か無かにすると、
                # 惜しいところまで直った文を捨てて元の悪い文に戻ってしまう。
                if retried and len(remaining) < len(problems):
                    text = retried
                    if remaining:
                        _say(f"[warn] {name}: 一部だけ改善 / {' / '.join(remaining)}")
                else:
                    _say(f"[warn] {name}: 書き直しで改善せず。初回の文を使います。")
                item["_pitch"] = text or None

            if not text:
                _say(f"[warn] {item.get('itemName')}: 空の応答が返りました。")
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で他の生成を止めない
            _say(f"[warn] {item.get('itemName')}: プレゼン文の生成に失敗しました ({exc})")
            item["_pitch"] = None

    return items


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="プレゼン文の生成")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIを呼ばずプロンプトだけ表示する（キーを使う前の確認用）",
    )
    parser.add_argument(
        "--input",
        help="候補JSON（data/candidates/YYYY-MM-DD.json）。省略時は最新を使う",
    )
    args = parser.parse_args()

    settings = config.load_settings()
    if args.input:
        path = config.ROOT / args.input
    else:
        files = sorted(store.CANDIDATES_DIR.glob("*.json"))
        if not files:
            print("候補JSONがありません。先に `python -m room.pipeline --local` を実行してください。")
            return 1
        path = files[-1]

    items = json.loads(path.read_text(encoding="utf-8"))
    generate(items, settings, dry_run=args.dry_run)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
