from btc_scanner import (
    resolve_direction_params,
    ATR_SL_MULT, ATR_TP_MULT, ATR_BE_MULT,
)


DEFAULTS = {"atr_sl_mult": ATR_SL_MULT, "atr_tp_mult": ATR_TP_MULT, "atr_be_mult": ATR_BE_MULT}


class TestResolveDirectionParams:
    def test_no_override_for_symbol_returns_defaults(self):
        assert resolve_direction_params({}, "BTCUSDT", "LONG") == DEFAULTS

    def test_flat_dict_long(self):
        overrides = {"BTCUSDT": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
        assert resolve_direction_params(overrides, "BTCUSDT", "LONG") == {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }

    def test_flat_dict_short_same_as_long(self):
        overrides = {"BTCUSDT": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
        assert resolve_direction_params(overrides, "BTCUSDT", "SHORT") == {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }

    def test_dedicated_long_and_short(self):
        overrides = {
            "DOGEUSDT": {
                "long":  {"atr_sl_mult": 0.7, "atr_tp_mult": 4.0, "atr_be_mult": 1.5},
                "short": {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
            }
        }
        assert resolve_direction_params(overrides, "DOGEUSDT", "LONG") == {
            "atr_sl_mult": 0.7, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }
        assert resolve_direction_params(overrides, "DOGEUSDT", "SHORT") == {
            "atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0,
        }

    def test_short_disabled(self):
        overrides = {
            "RUNEUSDT": {"long": {"atr_sl_mult": 0.7, "atr_tp_mult": 6.0, "atr_be_mult": 2.5},
                         "short": None}
        }
        assert resolve_direction_params(overrides, "RUNEUSDT", "SHORT") is None

    def test_long_disabled(self):
        overrides = {"XYZ": {"long": None, "short": {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0}}}
        assert resolve_direction_params(overrides, "XYZ", "LONG") is None

    def test_mix_flat_plus_partial_short_inherits(self):
        overrides = {
            "ETHUSDT": {
                "atr_sl_mult": 1.2, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
                "short": {"atr_sl_mult": 1.4, "atr_tp_mult": 3.0},
            }
        }
        assert resolve_direction_params(overrides, "ETHUSDT", "SHORT") == {
            "atr_sl_mult": 1.4, "atr_tp_mult": 3.0, "atr_be_mult": 1.5,
        }
        assert resolve_direction_params(overrides, "ETHUSDT", "LONG") == {
            "atr_sl_mult": 1.2, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }

    def test_flat_dict_only_short_block_missing_for_direction_uses_flat(self):
        overrides = {"SYM": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
        assert resolve_direction_params(overrides, "SYM", "SHORT") == {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }

    def test_empty_overrides_returns_defaults(self):
        assert resolve_direction_params({}, "ANY", "LONG") == DEFAULTS

    def test_overrides_is_none_returns_defaults(self):
        assert resolve_direction_params(None, "ANY", "LONG") == DEFAULTS

    def test_invalid_entry_type_returns_defaults(self):
        overrides = {"BTCUSDT": "garbage-string"}
        assert resolve_direction_params(overrides, "BTCUSDT", "LONG") == DEFAULTS

    def test_case_insensitive_direction(self):
        overrides = {"BTCUSDT": {"long":  {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}}
        expected = {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}
        assert resolve_direction_params(overrides, "BTCUSDT", "LONG") == expected
        assert resolve_direction_params(overrides, "BTCUSDT", "long") == expected
        assert resolve_direction_params(overrides, "BTCUSDT", "Long") == expected

    def test_dir_block_is_non_dict_non_null_falls_back_to_flat(self):
        overrides = {"SYM": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
                             "short": "wrong-type"}}
        assert resolve_direction_params(overrides, "SYM", "SHORT") == {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
        }

    def test_direction_none_returns_defaults(self):
        """direction=None cannot pick a block — return global defaults."""
        overrides = {"BTCUSDT": {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5}}
        assert resolve_direction_params(overrides, "BTCUSDT", None) == DEFAULTS
