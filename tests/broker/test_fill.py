import pytest

from twstock.broker.fill import FillModel, FullFill, VolumeParticipationFiller

# ── FillModel ABC ────────────────────────────────────────────────────────────


class TestFillModelIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            FillModel()


# ── VolumeParticipationFiller ────────────────────────────────────────────────


class TestVolumeParticipationFiller:
    def test_default_participation_rate_is_five_percent(self):
        filler = VolumeParticipationFiller()
        assert filler.participation_rate == 0.05

    def test_cap_math_applies_participation_rate_to_trailing_adv(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        assert filler.fillable(remaining=1_000_000, trailing_adv=100_000) == 5_000

    def test_order_exceeding_cap_fills_partially(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        result = filler.fillable(remaining=10_000, trailing_adv=100_000)
        assert result == 5_000
        assert result < 10_000

    def test_remaining_below_cap_fills_in_full(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        assert filler.fillable(remaining=100, trailing_adv=100_000) == 100

    def test_remaining_exactly_at_cap_fills_in_full(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        assert filler.fillable(remaining=5_000, trailing_adv=100_000) == 5_000

    def test_cap_floors_fractional_shares(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        # 0.05 * 333 = 16.65 -> floor to 16, not rounded to 17.
        assert filler.fillable(remaining=1_000, trailing_adv=333) == 16

    def test_zero_trailing_adv_fills_nothing(self):
        filler = VolumeParticipationFiller(participation_rate=0.05)
        assert filler.fillable(remaining=1_000, trailing_adv=0) == 0

    def test_custom_participation_rate_scales_cap(self):
        filler = VolumeParticipationFiller(participation_rate=0.10)
        assert filler.fillable(remaining=10_000, trailing_adv=100_000) == 10_000

    def test_same_rate_scales_with_differing_trailing_adv(self):
        # Same filler, same remaining, two bars with different trailing ADV:
        # the cap must track the volume input, not stay fixed.
        filler = VolumeParticipationFiller(participation_rate=0.05)
        low_volume_bar = filler.fillable(remaining=100_000, trailing_adv=1_000)
        high_volume_bar = filler.fillable(remaining=100_000, trailing_adv=1_000_000)
        assert low_volume_bar == 50
        assert high_volume_bar == 50_000
        assert high_volume_bar > low_volume_bar


# ── FullFill ──────────────────────────────────────────────────────────────────


class TestFullFill:
    def test_fills_entire_remaining(self):
        filler = FullFill()
        assert filler.fillable(remaining=12_345, trailing_adv=1) == 12_345

    def test_ignores_trailing_adv_even_when_zero(self):
        filler = FullFill()
        assert filler.fillable(remaining=500, trailing_adv=0) == 500

    def test_ignores_trailing_adv_even_when_small(self):
        filler = FullFill()
        assert filler.fillable(remaining=1_000_000, trailing_adv=1) == 1_000_000

    def test_zero_remaining_fills_zero(self):
        filler = FullFill()
        assert filler.fillable(remaining=0, trailing_adv=100_000) == 0
