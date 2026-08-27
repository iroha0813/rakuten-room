"""投稿前に見つかった実際の事故を再発させないためのテスト。

いずれも 2026-08-27 に生成された5件から見つかった実例。
1件だけがそのまま投稿できる状態で、4件に誤りがあった。
"""

from __future__ import annotations

from datetime import date

import pytest

from room import write

TODAY = date(2026, 8, 27)


class TestDealPeriod:
    """期限切れ・未開始のセールを本文に書かせない。"""

    def test_coupon_not_started_yet_is_outside(self):
        # 実例: 8/27 に「9/1〜9/13のクーポン」を訴求してしまった
        name = "＼55％OFFクーポン！9/1～9/13／ストロー整列！sakuraku 水切りラック"
        assert write.deal_period_status(name, TODAY) == "outside"

    def test_coupon_in_range_is_active(self):
        name = "＼55％OFFクーポン！8/24～8/30／水切りラック"
        assert write.deal_period_status(name, TODAY) == "active"

    def test_day_only_range_uses_the_same_month(self):
        name = "【8/24-27限定500円OFFクーポン】排気口カバー"
        assert write.deal_period_status(name, TODAY) == "active"

    def test_deadline_on_today_is_treated_as_expired(self):
        # 実例: 「27日正午まで半額」を27日の夕方に紹介しかけた
        name = "【27日正午まで半額！最大4,990円OFF】骨取りさば"
        assert write.deal_period_status(name, TODAY) == "outside"

    def test_future_deadline_is_active(self):
        assert write.deal_period_status("【30日まで半額】さば", TODAY) == "active"

    def test_marathon_without_dates_is_unverifiable(self):
        # 実例: お買い物マラソンは当日9:59に終わっていた
        name = "【マラソン限定！ 最大20%OFFクーポン】VAKUEN 真空保存容器"
        assert write.deal_period_status(name, TODAY) == "unverifiable"

    @pytest.mark.parametrize("word", ["タイムセール", "期間限定", "本日限り", "今だけ"])
    def test_other_unverifiable_wording(self, word):
        assert write.deal_period_status(f"【{word}】商品名", TODAY) == "unverifiable"

    def test_plain_product_name_is_active(self):
        assert write.deal_period_status("無洗米 あきたこまち 10kg", TODAY) == "active"

    def test_invalid_date_does_not_crash(self):
        assert write.deal_period_status("13/45～99/99 セール", TODAY) in {
            "active",
            "outside",
            "unverifiable",
        }


class TestDealGating:
    """期間が確認できないお得情報はモデルに渡さない。"""

    def _item(self, name):
        return {"itemName": name, "itemPrice": 3000, "itemCaption": "", "catchcopy": ""}

    def test_out_of_period_deal_is_hidden_from_the_model(self):
        item = self._item("＼55％OFFクーポン！9/1～9/13／水切りラック")
        prompt = write.build_prompt(item, "日用品", "切り口", True, TODAY)
        assert "55％OFF" not in prompt
        assert "クーポン" not in prompt
        assert item["_deal_status"] == "outside"

    def test_unverifiable_deal_is_hidden_from_the_model(self):
        item = self._item("【マラソン限定！ 最大20%OFFクーポン】真空保存容器")
        prompt = write.build_prompt(item, "日用品", "切り口", True, TODAY)
        assert "20%OFF" not in prompt
        assert "クーポン" not in prompt
        assert item["_deal_status"] == "unverifiable"

    def test_active_deal_reaches_the_model(self):
        item = self._item("【8/24-27限定クーポン】排気口カバー")
        prompt = write.build_prompt(item, "日用品", "切り口", True, TODAY)
        assert "クーポン" in prompt
        assert item["_deal_status"] == "active"


class TestUngroundedClaims:
    """商品情報にない実績を作らせない。"""

    def test_detects_invented_streak(self):
        # 実例: 「年間1位」と「上半期1位」(どちらも2024年) から「2年連続1位」を作った
        source = "楽天年間ランキング2024 第1位 楽天上半期ランキング2024 第1位"
        claims = write.ungrounded_claims("楽天で2年連続1位の実力です。", source)
        assert "2年連続" in claims

    def test_accepts_a_claim_written_in_the_source(self):
        source = "◇楽天唯一、最初から保護フィルムが付属 ◆楽天唯一、ラッピング無料"
        assert write.ungrounded_claims("楽天唯一のラッピング無料が嬉しい。", source) == []

    def test_accepts_no1_when_the_source_says_so(self):
        source = "【公式】MiNiPiC®【No.1受賞】レビュー9000件突破"
        assert write.ungrounded_claims("No.1受賞のキッズカメラ。", source) == []

    def test_detects_invented_superlative(self):
        source = "耐衝撃 強化ガラス クリアケース"
        assert "最強" in write.ungrounded_claims("透明なのに最強の耐衝撃性。", source)

    def test_ignores_spacing_and_dots(self):
        source = "No. 1 受賞"
        assert write.ungrounded_claims("No.1受賞です。", source) == []

    def test_review_flags_the_claim(self):
        source = "楽天年間ランキング2024 第1位"
        problems = write.review_pitch(
            "楽天で2年連続1位です。", 250, allow_deal_info=True, source=source
        )
        assert any("実績表現" in p for p in problems)

    def test_no_source_means_no_claim_check(self):
        assert write.review_pitch("最強です😊✨", 250, allow_deal_info=True) == []


class TestUnverifiableDealMasking:
    """「マラソン限定」を伏せ忘れて本文に書かれた事故の再発防止。

    伏せ字パターンに「マラソン」が入っておらず、期間を確認できないのに
    「マラソン限定でさらにお得」と書かれてしまった。
    """

    def test_marathon_is_masked_from_the_prompt(self):
        item = {
            "itemName": "【マラソン限定！ 最大20%OFFクーポン】VAKUEN 真空保存容器",
            "itemPrice": 5000,
            "catchcopy": "",
            "itemCaption": "",
        }
        prompt = write.build_prompt(item, "日用品", "切り口", True, TODAY)
        assert "マラソン" not in prompt

    def test_unverifiable_wording_is_rejected_in_the_output(self):
        assert write.contains_price("マラソン限定でお得です", allow_deal_info=False) is True
        assert write.contains_price("今だけお得です", allow_deal_info=False) is True

    def test_review_flags_it(self):
        problems = write.review_pitch("マラソン限定でさらにお得です。", 250, allow_deal_info=False)
        assert any("割引" in p or "金額" in p for p in problems)


class TestRankingClaims:
    """「ランキング1位」「受賞」も裏づけを確認する。

    「第◯位」しか見ていなかったため、「楽天ランキング1位」が
    検証をすり抜けていた。
    """

    def test_grounded_ranking_passes(self):
        source = "＼楽天 総合ランキング1位／（楽天で販売されている2億商品以上）"
        assert write.ungrounded_claims("楽天ランキング1位の真空保存容器✨", source) == []

    def test_invented_ranking_is_flagged(self):
        source = "耐衝撃 強化ガラス クリアケース"
        assert write.ungrounded_claims("ランキング1位の実力です✨", source)

    def test_invented_award_is_flagged(self):
        source = "キッズカメラ ミニピク"
        assert write.ungrounded_claims("グッドデザイン受賞のカメラ😊", source)

    def test_grounded_award_passes(self):
        source = "【公式】MiNiPiC®【No.1受賞】レビュー9000件突破"
        assert write.ungrounded_claims("No.1受賞のキッズカメラ😊✨", source) == []
