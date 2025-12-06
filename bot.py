import os
import logging
from pathlib import Path

import requests
from requests import RequestException
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# === ПУТИ И .env ===
BASE_DIR = Path(__file__).parent
# .env нужен только локально, на Render переменные окружения зададим в панели
load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")

# === СИМВОЛЫ НА BYBIT (SPOT USDC) ===
XRP_SYMBOL = "XRPUSDC"
XLM_SYMBOL = "XLMUSDC"

# === ЛОГИ ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =====================================================================
#  УТИЛИТЫ ДЛЯ BYBIT (PUBLIC API)
# =====================================================================

def get_spot_ticker(symbol: str) -> dict | None:
    """
    Bybit V5:
    GET /v5/market/tickers?category=spot&symbol=...
    Возвращает первый объект из list или None.
    """
    url = f"{BYBIT_BASE_URL}/v5/market/tickers"
    params = {
        "category": "spot",
        "symbol": symbol,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    result = data.get("result") or {}
    lst = result.get("list") or []
    if not lst:
        return None

    return lst[0]


def parse_prices_from_ticker(t: dict) -> tuple[float, float, float]:
    """
    Из тикера достаём:
    - lastPrice
    - bid1Price
    - ask1Price
    """
    last = float(t.get("lastPrice", 0.0))
    bid = float(t.get("bid1Price", 0.0))
    ask = float(t.get("ask1Price", 0.0))
    return last, bid, ask


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["/xstatus", "/xcycle_xrp 100"],
            ["/xcycle_xlm 100"],
        ],
        resize_keyboard=True,
    )


# =====================================================================
#  HANDLERS
# =====================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Привет!</b> 👋\n"
        "Я советник по спотовой торговле XRP и XLM на Bybit.\n\n"
        "Я <b>ничего сам не покупаю и не продаю</b>, а только показываю:\n"
        "• цены XRP/USDC и XLM/USDC\n"
        "• сколько XLM получишь за 1 XRP и наоборот\n"
        "• во сколько обходится круг XRP→XLM→XRP или XLM→XRP→XLM\n\n"
        "Команды:\n"
        "• <code>/xstatus</code> – текущие цены и соотношение XRP/XLM\n"
        "• <code>/xcycle_xrp 100</code> – круг XRP→XLM→XRP для 100 XRP\n"
        "• <code>/xcycle_xlm 100</code> – круг XLM→XRP→XLM для 100 XLM\n"
    )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def xstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показываем:
    - цены XRP/USDC и XLM/USDC
    - сколько XLM даёт 1 XRP (XRP→USDC по bid, потом USDC→XLM по ask)
    - сколько XRP даёт 1 XLM (XLM→USDC по bid, потом USDC→XRP по ask)
    - пример кругового результата с 1 XRP и 1 XLM
    """
    try:
        t_xrp = get_spot_ticker(XRP_SYMBOL)
        t_xlm = get_spot_ticker(XLM_SYMBOL)

        if not t_xrp or not t_xlm:
            await update.message.reply_text(
                "Не удалось получить тикеры XRPUSDC/XLMUSDC. "
                "Возможно, пары временно недоступны.",
                parse_mode="HTML",
            )
            return

        xrp_last, xrp_bid, xrp_ask = parse_prices_from_ticker(t_xrp)
        xlm_last, xlm_bid, xlm_ask = parse_prices_from_ticker(t_xlm)

        # --- Эффективные курсы конверта ---

        # 1) XRP -> USDC (продажа по bid), потом USDC -> XLM (покупка по ask)
        #    Сколько XLM за 1 XRP:
        xrp_to_xlm_rate = xrp_bid / xlm_ask if xlm_ask > 0 else 0.0

        # 2) XLM -> USDC (продажа по bid), потом USDC -> XRP (покупка по ask)
        #    Сколько XRP за 1 XLM:
        xlm_to_xrp_rate = xlm_bid / xrp_ask if xrp_ask > 0 else 0.0

        # --- Круговые сделки с 1 XRP и 1 XLM ---

        # Круг 1 XRP -> XLM -> XRP
        if xlm_ask > 0 and xrp_ask > 0:
            usdc1 = xrp_bid              # после продажи 1 XRP
            xlm1 = usdc1 / xlm_ask       # покупка XLM
            usdc2 = xlm1 * xlm_bid       # продажа XLM
            xrp2 = usdc2 / xrp_ask       # покупка XRP
            xrp_cycle_pct = (xrp2 - 1.0) * 100.0
        else:
            xrp2 = 0.0
            xrp_cycle_pct = 0.0

        # Круг 1 XLM -> XRP -> XLM
        if xrp_ask > 0 and xlm_ask > 0:
            usdc1_l = xlm_bid            # после продажи 1 XLM
            xrp1 = usdc1_l / xrp_ask     # покупка XRP
            usdc2_l = xrp1 * xrp_bid     # продажа XRP
            xlm2 = usdc2_l / xlm_ask     # покупка XLM
            xlm_cycle_pct = (xlm2 - 1.0) * 100.0
        else:
            xlm2 = 0.0
            xlm_cycle_pct = 0.0

        text = (
            "<b>Bybit Spot (USDC) – XRP и XLM</b>\n\n"
            f"<b>XRP/USDC</b>\n"
            f"• Last: <code>{xrp_last:.6f} USDC</code>\n"
            f"• Bid:  <code>{xrp_bid:.6f} USDC</code>\n"
            f"• Ask:  <code>{xrp_ask:.6f} USDC</code>\n\n"
            f"<b>XLM/USDC</b>\n"
            f"• Last: <code>{xlm_last:.6f} USDC</code>\n"
            f"• Bid:  <code>{xlm_bid:.6f} USDC</code>\n"
            f"• Ask:  <code>{xlm_ask:.6f} USDC</code>\n\n"
        )

        text += "<b>Эффективные курсы конверта через USDC:</b>\n"
        if xrp_to_xlm_rate > 0:
            text += (
                f"• При обмене XRP→XLM сейчас:\n"
                f"  1 XRP ≈ <code>{xrp_to_xlm_rate:.4f}</code> XLM\n"
            )
        if xlm_to_xrp_rate > 0:
            text += (
                f"• При обмене XLM→XRP сейчас:\n"
                f"  1 XLM ≈ <code>{xlm_to_xrp_rate:.6f}</code> XRP\n\n"
            )

        text += "<b>Пример круговых сделок:</b>\n"
        text += (
            f"• 1 XRP → XLM → XRP → ≈ <code>{xrp2:.6f}</code> XRP "
            f"(<code>{xrp_cycle_pct:.2f}%</code>)\n"
            f"• 1 XLM → XRP → XLM → ≈ <code>{xlm2:.6f}</code> XLM "
            f"(<code>{xlm_cycle_pct:.2f}%</code>)\n\n"
            "Обычно оба цикла дают <b>небольшой минус</b> из-за спреда и комиссий.\n"
            "Твоя задача – ловить моменты, когда перекос цен делает такой круг ближе к нулю "
            "или даже даёт плюс по количеству монет.\n\n"
            "Для точного расчёта своей суммы используй:\n"
            "<code>/xcycle_xrp 100</code> или <code>/xcycle_xlm 100</code>."
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    except RequestException:
        logger.exception("Сетевая ошибка в /xstatus")
        await update.message.reply_text(
            "❗️ Не удалось получить данные с Bybit (сетевая ошибка).\n"
            "Попробуй ещё раз через 30–60 секунд.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка в /xstatus")
        await update.message.reply_text(
            f"Ошибка при обработке команды: <code>{e}</code>",
            parse_mode="HTML",
        )


async def xcycle_xrp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /xcycle_xrp 100
    Круг XRP → XLM → XRP на заданное количество XRP.
    """
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/xcycle_xrp 100</code>",
            parse_mode="HTML",
        )
        return

    try:
        xrp_start = float(context.args[0].replace(",", "."))
        if xrp_start <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Не понял количество XRP 😅\nПример: <code>/xcycle_xrp 100</code>",
            parse_mode="HTML",
        )
        return

    try:
        t_xrp = get_spot_ticker(XRP_SYMBOL)
        t_xlm = get_spot_ticker(XLM_SYMBOL)

        if not t_xrp or not t_xlm:
            await update.message.reply_text(
                "Не удалось получить тикеры XRPUSDC/XLMUSDC.",
                parse_mode="HTML",
            )
            return

        xrp_last, xrp_bid, xrp_ask = parse_prices_from_ticker(t_xrp)
        xlm_last, xlm_bid, xlm_ask = parse_prices_from_ticker(t_xlm)

        if xlm_ask <= 0 or xrp_ask <= 0:
            await update.message.reply_text(
                "Некорректные данные по стаканам, не могу посчитать круг.",
                parse_mode="HTML",
            )
            return

        # 1) XRP -> USDC по bid XRPUSDC
        usdc1 = xrp_start * xrp_bid

        # 2) USDC -> XLM по ask XLMUSDC
        xlm_mid = usdc1 / xlm_ask

        # 3) XLM -> USDC по bid XLMUSDC
        usdc2 = xlm_mid * xlm_bid

        # 4) USDC -> XRP по ask XRPUSDC
        xrp_end = usdc2 / xrp_ask

        delta_xrp = xrp_end - xrp_start
        delta_pct = (delta_xrp / xrp_start * 100.0) if xrp_start > 0 else 0.0

        text = (
            "<b>Круг XRP → XLM → XRP (через USDC)</b>\n\n"
            f"Текущие цены:\n"
            f"• XRP/USDC bid/ask: <code>{xrp_bid:.6f}</code> / <code>{xrp_ask:.6f}</code>\n"
            f"• XLM/USDC bid/ask: <code>{xlm_bid:.6f}</code> / <code>{xlm_ask:.6f}</code>\n\n"
            f"<b>Старт:</b> <code>{xrp_start:.6f}</code> XRP\n\n"
            f"1) Продажа XRP по bid → USDC: ≈ <code>{usdc1:.6f}</code> USDC\n"
            f"2) Покупка XLM по ask → ≈ <code>{xlm_mid:.6f}</code> XLM\n"
            f"3) Продажа XLM по bid → ≈ <code>{usdc2:.6f}</code> USDC\n"
            f"4) Покупка XRP по ask → ≈ <code>{xrp_end:.6f}</code> XRP\n\n"
            "<b>Результат по XRP:</b>\n"
            f"• Изменение: <code>{delta_xrp:.6f}</code> XRP\n"
            f"• Доходность по кругу: <code>{delta_pct:.2f}%</code>\n\n"
            "Обычно результат чуть отрицательный (плата за спред и комиссии).\n"
            "Ищем ситуации, когда круг даёт близко к 0% или небольшой плюс, "
            "чтобы на серии таких сделок постепенно наращивать количество XRP."
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    except RequestException:
        logger.exception("Сетевая ошибка в /xcycle_xrp")
        await update.message.reply_text(
            "❗️ Не удалось получить данные с Bybit.\n"
            "Попробуй ещё раз через 30–60 секунд.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка в /xcycle_xrp")
        await update.message.reply_text(
            f"Ошибка при расчёте круга: <code>{e}</code>",
            parse_mode="HTML",
        )


async def xcycle_xlm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /xcycle_xlm 100
    Круг XLM → XRP → XLM на заданное количество XLM.
    """
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/xcycle_xlm 100</code>",
            parse_mode="HTML",
        )
        return

    try:
        xlm_start = float(context.args[0].replace(",", "."))
        if xlm_start <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Не понял количество XLM 😅\nПример: <code>/xcycle_xlm 100</code>",
            parse_mode="HTML",
        )
        return

    try:
        t_xrp = get_spot_ticker(XRP_SYMBOL)
        t_xlm = get_spot_ticker(XLM_SYMBOL)

        if not t_xrp or not t_xlm:
            await update.message.reply_text(
                "Не удалось получить тикеры XRPUSDC/XLMUSDC.",
                parse_mode="HTML",
            )
            return

        xrp_last, xrp_bid, xrp_ask = parse_prices_from_ticker(t_xrp)
        xlm_last, xlm_bid, xlm_ask = parse_prices_from_ticker(t_xlm)

        if xrp_ask <= 0 or xlm_ask <= 0:
            await update.message.reply_text(
                "Некорректные данные по стаканам, не могу посчитать круг.",
                parse_mode="HTML",
            )
            return

        # 1) XLM -> USDC по bid XLMUSDC
        usdc1 = xlm_start * xlm_bid

        # 2) USDC -> XRP по ask XRPUSDC
        xrp_mid = usdc1 / xrp_ask

        # 3) XRP -> USDC по bid XRPUSDC
        usdc2 = xrp_mid * xrp_bid

        # 4) USDC -> XLM по ask XLMUSDC
        xlm_end = usdc2 / xlm_ask

        delta_xlm = xlm_end - xlm_start
        delta_pct = (delta_xlm / xlm_start * 100.0) if xlm_start > 0 else 0.0

        text = (
            "<b>Круг XLM → XRP → XLM (через USDC)</b>\n\n"
            f"Текущие цены:\n"
            f"• XRP/USDC bid/ask: <code>{xrp_bid:.6f}</code> / <code>{xrp_ask:.6f}</code>\n"
            f"• XLM/USDC bid/ask: <code>{xlm_bid:.6f}</code> / <code>{xlm_ask:.6f}</code>\n\n"
            f"<b>Старт:</b> <code>{xlm_start:.6f}</code> XLM\n\n"
            f"1) Продажа XLM по bid → ≈ <code>{usdc1:.6f}</code> USDC\n"
            f"2) Покупка XRP по ask → ≈ <code>{xrp_mid:.6f}</code> XRP\n"
            f"3) Продажа XRP по bid → ≈ <code>{usdc2:.6f}</code> USDC\n"
            f"4) Покупка XLM по ask → ≈ <code>{xlm_end:.6f}</code> XLM\n\n"
            "<b>Результат по XLM:</b>\n"
            f"• Изменение: <code>{delta_xlm:.6f}</code> XLM\n"
            f"• Доходность по кругу: <code>{delta_pct:.2f}%</code>\n\n"
            "Аналогично, здесь виден реальный «налог» спреда и комиссий.\n"
            "Игра в том, чтобы находить моменты, когда перекос цен делает круг "
            "минимально убыточным или даёт плюс по количеству XLM."
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    except RequestException:
        logger.exception("Сетевая ошибка в /xcycle_xlm")
        await update.message.reply_text(
            "❗️ Не удалось получить данные с Bybit.\n"
            "Попробуй ещё раз через 30–60 секунд.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка в /xcycle_xlm")
        await update.message.reply_text(
            f"Ошибка при расчёте круга: <code>{e}</code>",
            parse_mode="HTML",
        )


# =====================================================================
#  MAIN
# =====================================================================

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в переменной окружения TELEGRAM_TOKEN")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("xstatus", xstatus))
    app.add_handler(CommandHandler("xcycle_xrp", xcycle_xrp))
    app.add_handler(CommandHandler("xcycle_xlm", xcycle_xlm))

    print("Bybit XRP/XLM advisor bot запущен.")
    app.run_polling()


if __name__ == "__main__":
    main()
