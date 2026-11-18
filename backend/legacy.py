"""Leftovers of the retired static strategies: names for the trade history and the exit rule of EMA-cross positions opened before the AI funnel."""
EMA_TEST = "ema_test"
EMA_TEST_TAG = "Cruce EMAs Especial"
EXIT_ON_CROSS = {"ema_cross", EMA_TEST}
EXIT_REASON = "CRUCE"
NAMES = {"trend_ema": "Tendencia EMA", "mean_reversion": "Reversión a la media", "breakout_turtle": "Ruptura Turtle", "supertrend_momentum": "Supertrend + momentum",
         "ema_pullback": "Pullback EMA21", "ema_cross": "Cruce EMA 10/20", EMA_TEST: "Test cruce EMA 10/20", "multi_horizon": "Tendencia Multi-Horizonte",
         "double_divergence": "Divergencia doble RSI+MACD", "fix_reversion": "Reversión fin de mes (fix)", "news_event": "Noticia macro", "manual": "Manual"}
INFO = {k: {"name": v, "rules": "Estrategia retirada (solo historial).", "basis": ""} for k, v in NAMES.items()}


def exit_signal(strategy: str, side: str, ema10: float, ema20: float) -> bool:
    if strategy not in EXIT_ON_CROSS:
        return False
    return ema10 < ema20 if side == "long" else ema10 > ema20
