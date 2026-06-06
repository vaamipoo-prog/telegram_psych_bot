import os
import re
import base64
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import anthropic
from database import (
    FREE_ANALYSES_LIMIT,
    init_db,
    check_subscription,
    add_subscription,
    remove_subscription,
    get_subscription_info,
    list_subscriptions,
    get_free_uses_count,
    increment_free_uses,
    has_activated_promo,
    activate_promo,
    get_memory,
    save_memory,
    clear_memory,
    add_person,
    get_people,
    get_person,
    delete_person,
    get_active_person,
    set_active_person,
    clear_active_person,
    get_person_history,
    save_person_history,
    clear_person_history,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "8781745517:AAH4kQnPN9s1JYdAnUgg4MMtdqxTGfqHIig")

ADMIN_IDS: set = set()
ADMIN_USERNAMES: set = set()
for _entry in os.getenv("ADMIN_IDS", "").split(","):
    _entry = _entry.strip().lstrip("@")
    if _entry.isdigit():
        ADMIN_IDS.add(int(_entry))
    elif _entry:
        ADMIN_USERNAMES.add(_entry.lower())

PROMO_CODE = "ДАША2026"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Role mapping ──────────────────────────────────────────────────────────────

ROLES = [
    ("💑 Парень / Муж",     "Парень/Муж"),
    ("👩‍❤️‍👨 Девушка / Жена", "Девушка/Жена"),
    ("💔 Бывший / Бывшая",  "Бывший/Бывшая"),
    ("👔 Начальник",         "Начальник"),
    ("💼 Коллега",           "Коллега"),
    ("👨‍👩‍👧 Родитель",       "Родитель"),
    ("🤝 Друг",              "Друг"),
    ("👩 Подруга",           "Подруга"),
    ("❓ Другое",            "Другое"),
]

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """КРИТИЧЕСКИ ВАЖНО: НИКОГДА НЕ ИСПОЛЬЗУЙ MARKDOWN. ЗАПРЕЩЕНО: *, **, ##, #, _, `. ТОЛЬКО ЧИСТЫЙ ТЕКСТ. Это правило важнее всего остального.

Ты — психолог и близкая подруга. Говоришь от себя, своими словами. Никогда не упоминаешь авторов, теории, книги, учёных — никаких "по Фрейду", "по Маслоу", "теория привязанности", "транзактный анализ" и подобного. Просто говоришь что видишь, как живой человек.

У тебя глубокие знания психологии: типы личности, манипуляции, привязанность, когнитивные искажения, язык тела в тексте, скрытые мотивы. Ты применяешь всё это внутри — но в ответе выдаёшь только живые наблюдения и выводы, без академических отсылок.

ЧТО ДЕЛАЕШЬ:
Смотришь на конкретные фразы из переписки. Говоришь что за человек перед тобой — его характер, паттерны, уязвимости. Видишь манипуляции — называешь их своими словами и объясняешь механизм. Понимаешь скрытые мотивы — что он на самом деле хочет. Даёшь конкретный совет: что ответить, как себя вести, на что обратить внимание.

СТИЛЬ:
Тёплый, прямой, честный. Как подруга, которая не будет тебя жалеть, но всегда на твоей стороне. Без воды, без общих слов. Цитируй конкретные фразы из переписки — это показывает что ты реально читала. Говори коротко и по делу. Только русский язык.

ФОРМАТ ОТВЕТА:
Пиши сплошным текстом, абзацами. Никаких списков с цифрами или буллетами. Никаких заголовков разделов. Просто живая речь — как будто рассказываешь подруге что увидела.

ДЛИНА: одно сообщение, максимум 3500 символов. Выбирай самое важное."""

DOSSIER_PROMPT = """На основе всей истории наших разговоров составь психологический портрет этого человека.

Расскажи: какой он как личность, какие у него паттерны поведения, что повторяется снова и снова. Какие манипуляции использует и как они работают. Что им движет — чего он на самом деле хочет. Что настораживает, а что нормально. Как лучше с ним общаться.

Говори от себя, живым языком, без авторов и теорий. Сплошным текстом, без списков и заголовков. Максимум 3500 символов. Только чистый текст, никакого markdown."""

WELCOME_TEXT = f"""👋 Привет! Я — твой персональный психолог-аналитик.

Анализирую переписки и помогаю разобраться:
кто перед тобой, какие техники влияния использует, что имеет в виду и как с ним общаться.

📋 Пришли переписку текстом или скриншот — разберу по полочкам.
👤 Используй /menu чтобы управлять профилями людей.

🎁 Новым пользователям {FREE_ANALYSES_LIMIT} бесплатных анализа.
Есть промокод? /promo <код>"""

SUBSCRIBE_TEXT = """💎 Для полного доступа необходима подписка.

Обратитесь к администратору для оформления.
Уже оплатили? Напишите администратору — он активирует подписку."""


# ── Persistent bottom keyboard ───────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("👥 Мои люди"),   KeyboardButton("📋 Досье")],
        [KeyboardButton("💳 Подписка"),   KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# ── Inline keyboards ──────────────────────────────────────────────────────────

def menu_kb(active_person: dict = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Добавить человека", callback_data="add_person"),
            InlineKeyboardButton("👥 Мои люди",          callback_data="my_people"),
        ],
        [
            InlineKeyboardButton("📁 Досье",             callback_data="dossier"),
            InlineKeyboardButton("🔄 Сменить человека",  callback_data="switch_person"),
        ],
    ])


def roles_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(ROLES), 2):
        row = []
        for label, _ in ROLES[i:i + 2]:
            # use role index as callback_data
            idx = next(j for j, (l, _) in enumerate(ROLES) if l == label)
            row.append(InlineKeyboardButton(label, callback_data=f"role_{idx}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
    return InlineKeyboardMarkup(rows)


def people_kb(people: list, active_id: int = None) -> InlineKeyboardMarkup:
    rows = []
    for p in people:
        mark = "✅ " if p["id"] == active_id else ""
        rows.append([InlineKeyboardButton(
            f"{mark}{p['name']} — {p['role']}",
            callback_data=f"sel_{p['id']}"
        )])
    rows.append([InlineKeyboardButton("➕ Добавить нового", callback_data="add_person")])
    rows.append([InlineKeyboardButton("🔙 Меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def person_actions_kb(person_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📁 Досье",              callback_data="dossier"),
            InlineKeyboardButton("🗑 Сбросить историю",   callback_data=f"clrhist_{person_id}"),
        ],
        [
            InlineKeyboardButton("❌ Удалить профиль",    callback_data=f"delperson_{person_id}"),
            InlineKeyboardButton("🔙 Меню",               callback_data="menu"),
        ],
    ])


def confirm_kb(yes_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data=yes_data),
        InlineKeyboardButton("❌ Отмена",      callback_data="menu"),
    ]])


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int, username: str = "") -> bool:
    return user_id in ADMIN_IDS or (username and username.lower() in ADMIN_USERNAMES)


def _strip_markdown(text: str) -> str:
    text = re.sub(r'```(?:[^\n]*\n)?([\s\S]*?)```', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'^\*\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'[*#_`]', '', text)
    return text


async def safe_reply(update: Update, text: str, reply_markup=None):
    clean = _strip_markdown(text)
    if len(clean) > 4000:
        clean = clean[:3997] + "..."
    await update.message.reply_text(
        clean,
        reply_markup=reply_markup if reply_markup is not None else MAIN_KEYBOARD,
    )


async def safe_edit(query, text: str, reply_markup=None):
    await query.edit_message_text(_strip_markdown(text), reply_markup=reply_markup)


def _get_user_memory(user_id: int) -> list:
    person = get_active_person(user_id)
    return get_person_history(person["id"]) if person else get_memory(user_id)


def _save_user_memory(user_id: int, messages: list):
    person = get_active_person(user_id)
    if person:
        save_person_history(person["id"], messages)
    else:
        save_memory(user_id, messages)


def _clear_user_memory(user_id: int) -> tuple:
    """Returns (cleared: bool, person_name: str|None)."""
    person = get_active_person(user_id)
    if person:
        return clear_person_history(person["id"]), person["name"]
    return clear_memory(user_id), None


def _call_claude(messages: list, system: str = None, max_tokens: int = 1500) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system or SYSTEM_PROMPT,
        messages=messages,
    )
    return _strip_markdown(response.content[0].text)


def _check_access(user_id: int) -> tuple:
    has_sub = check_subscription(user_id)
    has_free_left = get_free_uses_count(user_id) < FREE_ANALYSES_LIMIT
    return (has_sub or has_free_left), has_sub


async def _post_analysis_counter(update_or_query, has_sub: bool, user_id: int, is_query=False):
    if not has_sub:
        new_count = increment_free_uses(user_id)
        remaining = FREE_ANALYSES_LIMIT - new_count
        if remaining > 0:
            msg = f"🆓 Бесплатных анализов осталось: {remaining}"
        else:
            msg = (
                f"⚠️ Это был последний бесплатный анализ.\n"
                f"Для продолжения: /subscribe или /promo <код>"
            )
        if is_query:
            await update_or_query.message.reply_text(_strip_markdown(msg))
        else:
            await safe_reply(update_or_query, msg)


def _active_person_header(user_id: int) -> str:
    person = get_active_person(user_id)
    if person:
        return f"👤 Активный профиль: {person['name']} ({person['role']})"
    return "👤 Профиль не выбран"


# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_strip_markdown(WELCOME_TEXT), reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, WELCOME_TEXT)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    person = get_active_person(user_id)
    header = _active_person_header(user_id)
    await safe_reply(update, header, reply_markup=menu_kb(person))


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    cleared, person_name = _clear_user_memory(user_id)
    if cleared:
        if person_name:
            await safe_reply(update, f"🗑 История с {person_name} очищена. Пришли новую переписку.")
        else:
            await safe_reply(update, "🗑 Память очищена. Пришли новую переписку.")
    else:
        await safe_reply(update, "История и так пуста. Пришли переписку для анализа.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    info = get_subscription_info(user_id)
    if info and info["is_active"]:
        if info["expires_at"].year >= 2100:
            await safe_reply(update, "✅ Подписка активна: безлимитный доступ")
        else:
            expires = info["expires_at"].strftime("%d.%m.%Y %H:%M")
            await safe_reply(update, f"✅ Подписка активна до: {expires}")
    else:
        used = get_free_uses_count(user_id)
        remaining = max(0, FREE_ANALYSES_LIMIT - used)
        if remaining > 0:
            await safe_reply(update,
                f"🆓 Бесплатных анализов осталось: {remaining} из {FREE_ANALYSES_LIMIT}\n\n"
                f"Безлимитный доступ: /subscribe"
            )
        else:
            await safe_reply(update,
                f"❌ Бесплатные анализы использованы ({FREE_ANALYSES_LIMIT}/{FREE_ANALYSES_LIMIT})\n\n"
                f"Оформите подписку: /subscribe"
            )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, SUBSCRIBE_TEXT)


async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    if not context.args:
        await safe_reply(update, "Использование: /promo <промокод>")
        return
    code = context.args[0].upper().strip()
    if code != PROMO_CODE:
        await safe_reply(update, "❌ Неверный промокод.")
        return
    if has_activated_promo(user_id):
        await safe_reply(update, "ℹ️ Ты уже активировала этот промокод.")
        return
    activate_promo(user_id, username, code)
    await safe_reply(update,
        "🎉 Промокод активирован! Тебе открыт безлимитный бесплатный доступ.\n\n"
        "Просто отправляй переписки — я буду анализировать!"
    )


async def add_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id, update.effective_user.username or ""):
        await safe_reply(update, "⛔ Нет доступа.")
        return
    args = context.args
    if not args:
        await safe_reply(update, "Использование: /add_sub <user_id> [days=30] [username]")
        return
    try:
        user_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else 30
        username = args[2] if len(args) > 2 else ""
    except ValueError:
        await safe_reply(update, "❌ Неверный формат.\nИспользование: /add_sub <user_id> [days] [username]")
        return
    expires_at = add_subscription(user_id, username, days)
    expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
    await safe_reply(update,
        f"✅ Подписка добавлена.\n\nUser ID: {user_id}\nДней: {days}\nИстекает: {expires_str}"
    )


async def remove_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id, update.effective_user.username or ""):
        await safe_reply(update, "⛔ Нет доступа.")
        return
    args = context.args
    if not args:
        await safe_reply(update, "Использование: /remove_sub <user_id>")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await safe_reply(update, "❌ Неверный user_id.")
        return
    if remove_subscription(user_id):
        await safe_reply(update, f"✅ Подписка пользователя {user_id} удалена.")
    else:
        await safe_reply(update, f"❌ Пользователь {user_id} не найден в базе.")


async def list_subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id, update.effective_user.username or ""):
        await safe_reply(update, "⛔ Нет доступа.")
        return
    subs = list_subscriptions()
    if not subs:
        await safe_reply(update, "Подписок нет.")
        return
    lines = ["📋 Список подписок:\n"]
    for s in subs:
        status = "✅" if s["is_active"] else "❌"
        name = s["username"] or "—"
        expires = "безлимит" if s["expires_at"].year >= 2100 else s["expires_at"].strftime("%d.%m.%Y")
        lines.append(f"{status} {s['user_id']} (@{name}) до {expires}")
    await safe_reply(update, "\n".join(lines))


# ── Callback handler ──────────────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # ── Main menu ──
    if data == "menu":
        person = get_active_person(user_id)
        await safe_edit(query, _active_person_header(user_id), reply_markup=menu_kb(person))

    # ── Add person: start ──
    elif data == "add_person":
        context.user_data["awaiting_name"] = True
        await safe_edit(query,
            "Напиши имя человека (например: Алексей, Марина, Начальник):"
        )

    # ── Role selection ──
    elif data.startswith("role_"):
        idx = int(data.split("_")[1])
        role_display = ROLES[idx][1]
        name = context.user_data.pop("pending_person_name", "")
        if not name:
            await safe_edit(query, "Что-то пошло не так. Попробуй снова через /menu")
            return
        person_id = add_person(user_id, name, role_display)
        set_active_person(user_id, person_id)
        await safe_edit(query,
            f"✅ Профиль создан!\n\n"
            f"Имя: {name}\n"
            f"Роль: {role_display}\n\n"
            f"Теперь отправляй переписку — всё будет сохраняться к этому профилю.\n"
            f"Досье и управление: /menu",
        )

    # ── Cancel add person ──
    elif data == "cancel_add":
        context.user_data.pop("awaiting_name", None)
        context.user_data.pop("pending_person_name", None)
        person = get_active_person(user_id)
        await safe_edit(query, _active_person_header(user_id), reply_markup=menu_kb(person))

    # ── My people list ──
    elif data in ("my_people", "switch_person"):
        people = get_people(user_id)
        if not people:
            await safe_edit(query,
                "У тебя пока нет сохранённых профилей.\n\nДобавь первого человека:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("➕ Добавить человека", callback_data="add_person")
                ]])
            )
            return
        active = get_active_person(user_id)
        active_id = active["id"] if active else None
        await safe_edit(query, "Выбери человека:", reply_markup=people_kb(people, active_id))

    # ── Select person ──
    elif data.startswith("sel_"):
        person_id = int(data[4:])
        person = get_person(person_id)
        if not person or person["user_id"] != user_id:
            await safe_edit(query, "Профиль не найден.")
            return
        set_active_person(user_id, person_id)
        history = get_person_history(person_id)
        history_note = f"История: {len(history) // 2} анализов" if history else "История пуста — пришли первую переписку"
        await safe_edit(query,
            f"✅ Активирован: {person['name']} ({person['role']})\n{history_note}",
            reply_markup=person_actions_kb(person_id)
        )

    # ── Dossier ──
    elif data == "dossier":
        person = get_active_person(user_id)
        if not person:
            await safe_edit(query,
                "Сначала выбери человека через /menu",
                reply_markup=menu_kb()
            )
            return
        history = get_person_history(person["id"])
        if not history:
            await safe_edit(query,
                f"По {person['name']} пока нет данных.\n\nОтправь переписку с этим человеком — я проанализирую и начну собирать досье.",
                reply_markup=menu_kb(person)
            )
            return
        await query.edit_message_text(f"⏳ Составляю досье на {person['name']}...")
        try:
            dossier_messages = history + [{
                "role": "user",
                "content": DOSSIER_PROMPT
            }]
            result = _call_claude(dossier_messages, max_tokens=1500)
            await query.message.reply_text(
                f"📁 Досье: {person['name']} ({person['role']})\n\n{result}"
            )
        except anthropic.APIError as e:
            logger.error(f"Dossier API error: {e}")
            await query.message.reply_text("⚠️ Ошибка при составлении досье. Попробуй позже.")

    # ── Clear person history ──
    elif data.startswith("clrhist_"):
        person_id = int(data[8:])
        person = get_person(person_id)
        if person and person["user_id"] == user_id:
            clear_person_history(person_id)
            await safe_edit(query,
                f"🗑 История с {person['name']} очищена.\n\nМожешь присылать новые переписки.",
                reply_markup=person_actions_kb(person_id)
            )

    # ── Delete person (confirm) ──
    elif data.startswith("delperson_"):
        person_id = int(data[10:])
        person = get_person(person_id)
        if person and person["user_id"] == user_id:
            await safe_edit(query,
                f"Удалить профиль {person['name']} ({person['role']}) и всю его историю?",
                reply_markup=confirm_kb(f"confirmdel_{person_id}")
            )

    # ── Delete person (confirmed) ──
    elif data.startswith("confirmdel_"):
        person_id = int(data[11:])
        person = get_person(person_id)
        if person and person["user_id"] == user_id:
            name = person["name"]
            delete_person(person_id)
            active = get_active_person(user_id)
            await safe_edit(query,
                f"🗑 Профиль {name} удалён.",
                reply_markup=menu_kb(active)
            )


# ── Message handlers ──────────────────────────────────────────────────────────

async def analyze_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # ── Bottom keyboard buttons ──
    if text == "👥 Мои люди":
        people = get_people(user_id)
        active = get_active_person(user_id)
        active_id = active["id"] if active else None
        if not people:
            await safe_reply(update,
                "У тебя пока нет сохранённых профилей.\n\nДобавь через /menu — кнопка «Добавить человека»."
            )
        else:
            await update.message.reply_text(
                "Выбери человека:",
                reply_markup=people_kb(people, active_id)
            )
        return

    if text == "📋 Досье":
        person = get_active_person(user_id)
        if not person:
            await safe_reply(update, "Сначала выбери человека. Нажми «👥 Мои люди».")
            return
        history = get_person_history(person["id"])
        if not history:
            await safe_reply(update,
                f"По {person['name']} пока нет данных.\n\nОтправь переписку с этим человеком — начну собирать досье."
            )
            return
        await update.message.chat.send_action("typing")
        try:
            dossier_messages = history + [{"role": "user", "content": DOSSIER_PROMPT}]
            result = _call_claude(dossier_messages, max_tokens=1500)
            await safe_reply(update, f"📁 Досье: {person['name']} ({person['role']})\n\n{result}")
        except anthropic.APIError as e:
            logger.error(f"Dossier error: {e}")
            await safe_reply(update, "⚠️ Ошибка при составлении досье. Попробуй позже.")
        return

    if text == "💳 Подписка":
        info = get_subscription_info(user_id)
        if info and info["is_active"]:
            if info["expires_at"].year >= 2100:
                await safe_reply(update, "✅ Подписка: безлимитный доступ")
            else:
                expires = info["expires_at"].strftime("%d.%m.%Y %H:%M")
                await safe_reply(update, f"✅ Подписка активна до: {expires}")
        else:
            used = get_free_uses_count(user_id)
            remaining = max(0, FREE_ANALYSES_LIMIT - used)
            if remaining > 0:
                await safe_reply(update,
                    f"🆓 Бесплатных анализов осталось: {remaining} из {FREE_ANALYSES_LIMIT}\n\n"
                    f"Для безлимитного доступа: /subscribe"
                )
            else:
                await safe_reply(update,
                    f"❌ Бесплатные анализы закончились ({FREE_ANALYSES_LIMIT}/{FREE_ANALYSES_LIMIT})\n\n"
                    f"Оформите подписку: /subscribe"
                )
        return

    if text == "❓ Помощь":
        await safe_reply(update,
            "Если у тебя есть вопросы, напиши сюда @kosmoooos0"
        )
        return

    # ── State: waiting for person name ──
    if context.user_data.get("awaiting_name"):
        name = text.strip()
        if len(name) < 1 or len(name) > 50:
            await safe_reply(update, "Имя должно быть от 1 до 50 символов. Введи ещё раз:")
            return
        context.user_data["awaiting_name"] = False
        context.user_data["pending_person_name"] = name
        await update.message.reply_text(
            f"Имя: {name}\n\nТеперь выбери роль:",
            reply_markup=roles_kb()
        )
        return

    # ── Normal analysis / follow-up ──
    memory = _get_user_memory(user_id)
    is_new_analysis = not memory

    if is_new_analysis:
        allowed, has_sub = _check_access(user_id)
        if not allowed:
            await safe_reply(update,
                f"🔒 Бесплатные анализы закончились ({FREE_ANALYSES_LIMIT}/{FREE_ANALYSES_LIMIT}).\n\n"
                f"Оформите подписку: /subscribe\n"
                f"Или активируйте промокод: /promo <код>"
            )
            return
        if len(text) < 20:
            await safe_reply(update,
                "Пожалуйста, отправь переписку (хотя бы несколько сообщений) для анализа."
            )
            return
        person = get_active_person(user_id)
        if person:
            user_content = (
                f"Анализирую переписку. Контекст: это общение с {person['name']} ({person['role']}).\n\n"
                f"{text}"
            )
        else:
            user_content = f"Проанализируй эту переписку:\n\n{text}"
    else:
        has_sub = check_subscription(user_id)
        user_content = text

    messages = memory + [{"role": "user", "content": user_content}]
    await update.message.chat.send_action("typing")

    try:
        result = _call_claude(messages)
        messages.append({"role": "assistant", "content": result})
        _save_user_memory(user_id, messages)
        await safe_reply(update, result)

        if is_new_analysis:
            await _post_analysis_counter(update, has_sub, user_id)
            person = get_active_person(user_id)
            if person:
                hint = f"💬 Задавай вопросы об этом человеке — я помню всё.\n📁 Досье: /menu → Досье"
            else:
                hint = "💬 Задавай любые вопросы — я помню контекст.\nНовый человек: /forget или /menu"
            await safe_reply(update, hint)

    except anthropic.APIError as e:
        logger.error(f"API error: {e}")
        await safe_reply(update, "⚠️ Ошибка при анализе. Попробуй ещё раз позже.")


async def analyze_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = _get_user_memory(user_id)
    is_new_analysis = not memory

    if is_new_analysis:
        allowed, has_sub = _check_access(user_id)
        if not allowed:
            await safe_reply(update,
                f"🔒 Бесплатные анализы закончились ({FREE_ANALYSES_LIMIT}/{FREE_ANALYSES_LIMIT}).\n\n"
                f"Оформите подписку: /subscribe"
            )
            return
    else:
        has_sub = check_subscription(user_id)

    await update.message.chat.send_action("typing")

    try:
        if update.message.photo:
            tg_file = await update.message.photo[-1].get_file()
            media_type = "image/jpeg"
        else:
            doc = update.message.document
            media_type = doc.mime_type if doc.mime_type in (
                "image/jpeg", "image/png", "image/gif", "image/webp"
            ) else "image/jpeg"
            tg_file = await doc.get_file()

        image_bytes = await tg_file.download_as_bytearray()
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        person = get_active_person(user_id)
        photo_text = "Прочитай переписку на скриншоте и сделай психологический анализ."
        if person:
            photo_text += f" Контекст: это общение с {person['name']} ({person['role']})."

        photo_messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": photo_text},
            ],
        }]

        result = _call_claude(photo_messages)

        new_memory = memory + [
            {"role": "user", "content": "[Скриншот переписки]"},
            {"role": "assistant", "content": result},
        ]
        _save_user_memory(user_id, new_memory)

        await safe_reply(update, result)

        if is_new_analysis:
            await _post_analysis_counter(update, has_sub, user_id)
            await safe_reply(update,
                "💬 Задавай вопросы — я помню анализ.\n"
                "Управление профилями: /menu"
            )

    except anthropic.APIError as e:
        logger.error(f"API error: {e}")
        await safe_reply(update, "⚠️ Ошибка при анализе. Попробуй ещё раз позже.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_command))
    app.add_handler(CommandHandler("menu",        menu_command))
    app.add_handler(CommandHandler("forget",      forget_command))
    app.add_handler(CommandHandler("status",      status_command))
    app.add_handler(CommandHandler("subscribe",   subscribe_command))
    app.add_handler(CommandHandler("promo",       promo_command))
    app.add_handler(CommandHandler("add_sub",     add_sub_command))
    app.add_handler(CommandHandler("remove_sub",  remove_sub_command))
    app.add_handler(CommandHandler("list_subs",   list_subs_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_message))
    app.add_handler(MessageHandler(filters.PHOTO, analyze_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, analyze_photo))

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
