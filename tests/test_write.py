"""プレゼン文生成のテスト。Claude API は呼ばない。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from room import write

SETTINGS = {
    "llm": {"model": "claude-haiku-4-5", "max_tokens": 512, "max_chars": 200},
    "genres": {"food": {"label": "食品"}},
}

DEAL_SETTINGS = {
    **SETTINGS,
    "llm": {**SETTINGS["llm"], "allow_deal_info": True},
}


def make_item(**overrides):
    item = {
        "itemName": "無洗米 あきたこまち 10kg",
        "itemPrice": 5950,
        "shopName": "ハーベストシーズン",
        "reviewCount": 65235,
        "reviewAverage": 4.68,
        "itemCaption": "今なら送料無料、通常5,950円のところ特価！",
        "_genre_key": "food",
    }
    item.update(overrides)
    return item


class FakeClient:
    """指定したテキストを順に返すスタブ。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.messages = self

    def create(self, *, model, max_tokens, system, messages):
        self.calls.append(messages)
        text = self.replies.pop(0) if self.replies else ""

        class Block:
            type = "text"

        block = Block()
        block.text = text

        class Response:
            content = [block]

        return Response()


class TestContainsPrice:
    @pytest.mark.parametrize(
        "text",
        [
            "1,980円でこの内容",
            "¥1980はお得",
            "１９８０円です",
            "30%OFFで買えます",
            "50％オフ",
            "500ポイント還元",
        ],
    )
    def test_detects_price_expressions(self, text):
        assert write.contains_price(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "毎日使うものだから、失敗したくない人に選ばれています",
            "レビュー6万件超の定番です",  # 件数は金額ではない
            "10kgでこの手軽さ",
            "",
        ],
    )
    def test_ignores_non_price_numbers(self, text):
        assert write.contains_price(text) is False


class TestPriceBand:
    def test_maps_to_qualitative_labels(self):
        assert write.price_band(980) == "気軽に試せる価格帯"
        assert write.price_band(5950) == "日用品としては少し奮発する価格帯"
        assert write.price_band(20000) == "じっくり選んで買う価格帯"
        assert write.price_band(99000) == write.PRICE_BAND_TOP

    def test_boundaries_are_exclusive_upward(self):
        assert write.price_band(2999) == "気軽に試せる価格帯"
        assert write.price_band(3000) == "日用品としては少し奮発する価格帯"


class TestStripPrices:
    def test_masks_prices_embedded_in_product_names(self):
        名前 = "＼81%OFF＆P2倍で2,174円！／ワイヤレスイヤホン"
        masked = write.strip_prices(名前)
        assert "2,174円" not in masked
        assert "81%OFF" not in masked
        assert "ワイヤレスイヤホン" in masked

    def test_leaves_non_price_text_alone(self):
        assert write.strip_prices("10kg 5kg×2袋 無洗米") == "10kg 5kg×2袋 無洗米"


class TestBuildPrompt:
    def test_never_passes_the_numeric_price(self):
        """商品説明や商品名に埋まった金額も渡さないこと。"""
        prompt = write.build_prompt(make_item(), "食品", "切り口")
        assert "5,950" not in prompt
        assert "5950" not in prompt
        assert "価格の質感" in prompt

    def test_masks_prices_and_deals_from_the_caption(self):
        """既定ではお得情報ごと伏せる。"""
        item = make_item(itemCaption="通常3,980円が今だけ50%OFF！送料無料でお届け")
        prompt = write.build_prompt(item, "食品", "切り口")
        assert "3,980" not in prompt
        assert "50%OFF" not in prompt
        assert "送料無料" not in prompt
        assert "お届け" in prompt  # 金額以外の説明は残す

    def test_includes_review_metrics_and_angle(self):
        prompt = write.build_prompt(make_item(), "食品", "定番として選ばれ続けている理由")
        assert "65235" in prompt
        assert "4.68" in prompt
        assert "定番として選ばれ続けている理由" in prompt

    def test_omits_empty_fields(self):
        prompt = write.build_prompt(make_item(catchcopy=None, itemCaption=""), "食品", "x")
        assert "キャッチコピー" not in prompt
        assert "商品説明" not in prompt


class TestReviewPitch:
    def test_clean_text_passes(self):
        text = "毎朝のお米を研ぐ手間がもう不要😊\n\n無洗米だから朝が楽になります✨\n#無洗米"
        assert write.review_pitch(text, 200) == []

    def test_flags_price(self):
        problems = write.review_pitch("1,980円でこの内容。", 200)
        assert any("金額" in p for p in problems)

    def test_flags_length(self):
        problems = write.review_pitch("あ" * 250, 200)
        assert any("250字" in p for p in problems)

    def test_flags_first_line_cut_mid_sentence(self):
        problems = write.review_pitch("毎日の洗濯だからこそ、\n信頼できるものを。", 200)
        assert any("1行目" in p for p in problems)

    def test_accepts_first_line_ending_in_a_full_stop(self):
        assert write.review_pitch("毎日の洗濯に信頼を。\n続きます😊✨", 200) == []

    def test_accepts_question_and_exclamation(self):
        assert write.review_pitch("油汚れで困っていませんか？\n本文です😊✨", 200) == []
        assert write.review_pitch("これは便利！\n本文です😊✨", 200) == []

    def test_flags_first_line_too_long_for_the_feed(self):
        long_line = "あ" * 60 + "。"
        problems = write.review_pitch(long_line + "\n本文。", 500)
        assert any("1行目が" in p and "字あります" in p for p in problems)

    def test_flags_review_metrics_in_the_first_line(self):
        problems = write.review_pitch("4000件超のレビューで支持される定番です。\n本文。", 200)
        assert any("1行目にレビュー件数" in p for p in problems)

    def test_allows_review_metrics_later(self):
        text = "毎日の洗濯を楽にしたい人へ😊\n4000件超のレビューが実力を物語ります✨"
        assert write.review_pitch(text, 200) == []

    def test_reports_several_problems_at_once(self):
        problems = write.review_pitch("1,980円だから、\n" + "あ" * 250, 200)
        # 金額・字数・1行目の途中切れ・絵文字なし
        assert len(problems) == 4


class TestGenerate:
    def test_accepts_clean_output_without_retry(self, monkeypatch):
        client = FakeClient(["毎日のごはんを楽にしたい人へ😊 無洗米は洗う手間が消えます🍚\n#時短"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert "無洗米" in items[0]["_pitch"]
        assert len(client.calls) == 1

    def test_retries_once_when_price_leaks(self, monkeypatch, capsys):
        client = FakeClient(
            ["5,950円でこの内容はお得です。", "毎日のごはんを楽にしたい人へ。洗う手間が消えます🍚"]
        )
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)

        assert write.contains_price(items[0]["_pitch"]) is False
        assert len(client.calls) == 2
        assert "書き直します" in capsys.readouterr().out
        # 2回目は会話を続けて直させている
        assert client.calls[1][1]["role"] == "assistant"

    def test_retries_when_too_long(self, monkeypatch):
        client = FakeClient(["あ" * 260 + "。", "短く直しました😊✨"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert items[0]["_pitch"] == "短く直しました😊✨"

    def test_retries_when_first_line_is_cut(self, monkeypatch):
        client = FakeClient(["毎日の洗濯だからこそ、\n信頼を。", "毎日の洗濯に信頼を。\n本文です😊✨"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert items[0]["_pitch"].split("\n")[0] == "毎日の洗濯に信頼を。"

    def test_keeps_text_when_retry_does_not_improve(self, monkeypatch, capsys):
        client = FakeClient(["5,950円です。", "やはり5,950円です。"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert items[0]["_pitch"] == "5,950円です。"
        assert "改善せず" in capsys.readouterr().out

    def test_accepts_partial_improvement(self, monkeypatch, capsys):
        """完璧でなくても指摘が減ったなら採用する。

        全か無かにすると、惜しいところまで直った文を捨てて
        元の悪い文へ戻ってしまう。
        """
        bad = "1,980円だから、\n" + "あ" * 250  # 金額 + 字数 + 1行目途中切れ + 絵文字なし
        better = "毎日を楽にしたい人へ😊\n" + "あ" * 250 + "✨"  # 字数超過だけ残る
        client = FakeClient([bad, better])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)

        assert items[0]["_pitch"] == better
        assert "一部だけ改善" in capsys.readouterr().out

    def test_one_failure_does_not_stop_the_rest(self, monkeypatch, capsys):
        class Flaky(FakeClient):
            def create(self, **kwargs):
                if not self.calls:
                    self.calls.append(kwargs["messages"])
                    raise RuntimeError("boom")
                return super().create(**kwargs)

        client = Flaky(["2件目は成功"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item(), make_item(itemName="別の商品")]
        write.generate(items, SETTINGS)

        assert items[0]["_pitch"] is None
        assert items[1]["_pitch"] == "2件目は成功"
        assert "失敗しました" in capsys.readouterr().out

    def test_result_survives_a_crash_in_the_retry_path(self, monkeypatch):
        """API呼び出しが成功していれば、後段で例外が出ても文章を失わないこと。

        警告の print が UnicodeEncodeError を投げて生成結果ごと捨てられる
        事故が実際に起きたため、その再発を止める。
        """
        client = FakeClient(["1,980円です。"])  # 書き直し対象になる
        monkeypatch.setattr(write, "_client", lambda: client)

        def boom(*args, **kwargs):
            raise UnicodeEncodeError("cp932", "—", 0, 1, "illegal multibyte sequence")

        monkeypatch.setattr("builtins.print", boom)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert items[0]["_pitch"] == "1,980円です。"

    def test_dry_run_makes_no_api_call(self, monkeypatch, capsys):
        def boom():
            raise AssertionError("dry-run でAPIクライアントを作ってはいけない")

        monkeypatch.setattr(write, "_client", boom)
        items = [make_item()]
        write.generate(items, SETTINGS, dry_run=True)
        assert items[0]["_pitch"] is None
        assert "価格の質感" in capsys.readouterr().out

    def test_angles_rotate_across_items(self, monkeypatch):
        client = FakeClient(["a", "b", "c"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item(), make_item(), make_item()]
        write.generate(items, SETTINGS)
        angles = [i["_angle"] for i in items]
        assert len(set(angles)) == 3


class TestSystemPrompt:
    def _render(self, deal_rule=None):
        return write.SYSTEM_PROMPT.format(
            max_chars=200, deal_rule=deal_rule or write.DEAL_RULE_STRICT
        )

    def test_states_the_feed_truncation_constraint(self):
        system = self._render()
        assert "40〜60字" in system
        assert "続きを読む" in system

    def test_forbids_prices_and_fabricated_experience(self):
        system = self._render()
        assert "金額・価格・割引率・ポイント倍率・クーポンを書かない" in system
        assert "創作しない" in system

class TestDealInfo:
    """お得情報の解禁。ROOMで最も反応が取れる訴求だが、絶対額だけは常に禁止。"""

    def test_absolute_yen_is_always_rejected(self):
        assert write.contains_price("1,980円です", allow_deal_info=True) is True
        assert write.contains_price("¥1980です", allow_deal_info=True) is True

    @pytest.mark.parametrize(
        "text", ["クーポンあり！", "送料無料です", "30%OFF", "ポイント10倍", "今だけ半額"]
    )
    def test_deals_pass_only_when_allowed(self, text):
        assert write.contains_price(text, allow_deal_info=False) is True
        assert write.contains_price(text, allow_deal_info=True) is False

    def test_strip_keeps_deals_when_allowed(self):
        raw = "＼81%OFF＆P2倍で2,174円！／ワイヤレスイヤホン"
        allowed = write.strip_prices(raw, allow_deal_info=True)
        assert "2,174円" not in allowed
        assert "81%OFF" in allowed

    def test_strip_removes_deals_when_not_allowed(self):
        raw = "＼81%OFF＆P2倍で2,174円！／ワイヤレスイヤホン"
        strict = write.strip_prices(raw, allow_deal_info=False)
        assert "2,174円" not in strict
        assert "81%OFF" not in strict

    def test_prompt_keeps_deals_for_the_model(self):
        item = make_item(itemCaption="今だけクーポンで3,980円！送料無料")
        prompt = write.build_prompt(item, "食品", "切り口", allow_deal_info=True)
        assert "3,980" not in prompt
        assert "クーポン" in prompt
        assert "送料無料" in prompt

    def test_deal_angle_is_only_used_when_allowed(self, monkeypatch):
        client = FakeClient(["a"] * 8)
        monkeypatch.setattr(write, "_client", lambda: client)

        strict_items = [make_item() for _ in range(7)]
        write.generate(strict_items, SETTINGS)
        assert write.DEAL_ANGLE not in [i["_angle"] for i in strict_items]

        client.replies = ["a"] * 8
        deal_items = [make_item() for _ in range(7)]
        write.generate(deal_items, DEAL_SETTINGS)
        assert write.DEAL_ANGLE in [i["_angle"] for i in deal_items]

    def test_system_prompt_switches_rule(self):
        strict = write.SYSTEM_PROMPT.format(
            max_chars=200, deal_rule=write.DEAL_RULE_STRICT
        )
        allowed = write.SYSTEM_PROMPT.format(
            max_chars=200, deal_rule=write.DEAL_RULE_ALLOWED
        )
        assert "クーポンを書かない" in strict
        assert "お得情報は書いてよい" in allowed
        assert "でっち上げてはいけない" in allowed

    def test_generate_accepts_a_coupon_hook(self, monkeypatch):
        client = FakeClient(["クーポンありでお得に試せます😊\n本文です✨\n#時短"])
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, DEAL_SETTINGS)
        assert items[0]["_pitch"].startswith("クーポンあり")
        assert len(client.calls) == 1  # 書き直しが走らない


class TestEmojiDecoration:
    """ROOMのフィードでは装飾のない説明文は読み飛ばされる。

    禁止事項を積み増した結果、生成文から絵文字が完全に消えた回帰があった。
    """

    def test_counts_emoji_and_symbols(self):
        assert write.count_emoji("便利です😊 おすすめ✨") == 2
        assert write.count_emoji("味も◎ 見た目も♡") == 2
        assert write.count_emoji("絵文字なしの説明文です。") == 0

    def test_flags_plain_text(self):
        text = "毎日の洗濯を楽にしたい人へ。\n信頼できる定番です。"
        problems = write.review_pitch(text, 250)
        assert any("絵文字が0個" in p for p in problems)

    def test_accepts_moderate_decoration(self):
        text = "毎日の洗濯を楽にしたい人へ😊\n信頼できる定番です✨"
        assert write.review_pitch(text, 250) == []

    def test_flags_too_many(self):
        text = "楽になります" + "😊" * 12
        problems = write.review_pitch(text, 250)
        assert any("多すぎる" in p for p in problems)

    def test_retries_when_decoration_is_missing(self, monkeypatch):
        client = FakeClient(
            ["毎日の洗濯を楽にしたい人へ。定番です。", "毎日の洗濯を楽にしたい人へ😊\n定番です✨"]
        )
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item()]
        write.generate(items, SETTINGS)
        assert write.count_emoji(items[0]["_pitch"]) >= write.MIN_EMOJI


class TestStylePrompt:
    def test_shows_concrete_room_examples(self):
        system = write.SYSTEM_PROMPT.format(
            max_chars=250, deal_rule=write.DEAL_RULE_STRICT
        )
        assert "文体" in system
        assert "絵文字を2〜5個使う" in system
        # 装飾を誇張の道具にさせない一文が残っていること
        assert "装飾は温度であって内容ではありません" in system


class TestVariationPlan:
    """切り口と型が順位で固定されず、日ごとに振り直されること。"""

    def test_no_repeat_within_a_day(self):
        plan = write.plan_variations(5, day=date(2026, 9, 1))
        angles = [angle for angle, _ in plan]
        formats = [fmt["key"] for _, fmt in plan]
        assert len(set(angles)) == 5
        assert len(set(formats)) == 5

    def test_same_day_is_reproducible(self):
        first = write.plan_variations(5, day=date(2026, 9, 1))
        second = write.plan_variations(5, day=date(2026, 9, 1))
        assert first == second

    def test_different_days_differ(self):
        """順位で決めていた頃は毎日まったく同じ並びだった。"""
        days = [date(2026, 9, d) for d in range(1, 8)]
        orders = {tuple(a for a, _ in write.plan_variations(5, day=d)) for d in days}
        assert len(orders) > 1

    def test_third_slot_is_not_always_the_gift_angle(self):
        """3番目が毎日「贈り物」枠になり、スポンジやノートPCが贈答品にされていた。"""
        third = {write.plan_variations(5, day=date(2026, 9, d))[2][0] for d in range(1, 15)}
        assert len(third) > 1

    def test_every_angle_gets_used_eventually(self):
        """以前は末尾の切り口とお得情報の切り口が一度も使われなかった。"""
        used = set()
        for offset in range(30):
            plan = write.plan_variations(
                5, allow_deal_info=True, day=date(2026, 9, 1) + timedelta(days=offset)
            )
            used.update(angle for angle, _ in plan)
        assert used == set(write.ANGLES + [write.DEAL_ANGLE])

    def test_deal_angle_excluded_when_deals_are_off(self):
        for offset in range(30):
            plan = write.plan_variations(5, day=date(2026, 9, 1) + timedelta(days=offset))
            assert all(angle != write.DEAL_ANGLE for angle, _ in plan)

    def test_recently_used_formats_go_last(self):
        day = date(2026, 9, 10)
        recent = [
            {"presentedAt": (day - timedelta(days=1)).isoformat(), "format": key}
            for key in ("hook_reason", "question", "scene")
        ]
        plan = write.plan_variations(3, history=recent, day=day)
        assert {fmt["key"] for _, fmt in plan}.isdisjoint({"hook_reason", "question", "scene"})

    def test_stale_history_is_ignored(self):
        day = date(2026, 9, 10)
        old = [
            {"presentedAt": (day - timedelta(days=99)).isoformat(), "format": key}
            for key in ("hook_reason", "question", "scene")
        ]
        assert write.plan_variations(3, history=old, day=day) == write.plan_variations(
            3, day=day
        )

    def test_more_items_than_formats_wraps_instead_of_failing(self):
        plan = write.plan_variations(9, day=date(2026, 9, 1))
        assert len(plan) == 9


class TestFormatPrompt:
    def test_prompt_carries_the_assigned_format(self):
        fmt = next(f for f in write.FORMATS if f["key"] == "oneline")
        prompt = write.build_prompt(make_item(), "食品", "定番の理由", fmt=fmt)
        assert "短文で言い切る" in prompt
        assert "ハッシュタグは1個まで" in prompt

    def test_falls_back_to_the_default_format(self):
        prompt = write.build_prompt(make_item(), "食品", "定番の理由")
        assert "指定された型" in prompt

    def test_structure_lives_in_the_prompt_not_the_system(self):
        """型ごとに変わる部分がシステムプロンプトに残っていないこと。"""
        system = write.SYSTEM_PROMPT.format(
            max_chars=250, deal_rule=write.DEAL_RULE_STRICT
        )
        assert "ハッシュタグを最大3個" not in system


class TestHashtagCount:
    def test_counts_both_ascii_and_fullwidth(self):
        assert write.count_hashtags("本文\n#時短 ＃キッチン") == 2

    def test_flags_too_many_for_the_format(self):
        text = "毎日が楽になります😊\n定番です✨\n#時短 #キッチン #便利"
        problems = write.review_pitch(text, 250, hashtags=1)
        assert any("ハッシュタグが3個" in p for p in problems)

    def test_accepts_within_the_format_limit(self):
        text = "毎日が楽になります😊\n定番です✨\n#時短"
        assert write.review_pitch(text, 250, hashtags=1) == []

    def test_flags_missing_hashtags(self):
        text = "毎日が楽になります😊\n定番です✨"
        problems = write.review_pitch(text, 250, hashtags=2)
        assert any("ハッシュタグがありません" in p for p in problems)

    def test_unchecked_when_no_format_limit_given(self):
        text = "毎日が楽になります😊\n定番です✨"
        assert write.review_pitch(text, 250) == []


class TestVariationIsRecorded:
    def test_generate_tags_each_item(self, monkeypatch):
        client = FakeClient(["毎日の洗濯が楽になります😊\n定番です✨\n#時短"] * 2)
        monkeypatch.setattr(write, "_client", lambda: client)
        items = [make_item(), make_item(itemName="別の商品")]
        write.generate(items, SETTINGS, day=date(2026, 9, 1))
        assert all(item["_angle"] and item["_format"] for item in items)
        assert items[0]["_format"] != items[1]["_format"]
