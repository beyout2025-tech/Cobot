import os
import json
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- إعدادات البوت والبيئة ---
TOKEN = os.getenv("BOT_TOKEN")
SUDO_ID = 7607952642  # معرف المطور الأساسي

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
# إضافة التخزين للمؤقت لنظام FSM دون المساس بالـ Dispatcher الأصلي
dp = Dispatcher(storage=MemoryStorage())

# --- [Extension] تعريف حالات FSM للميزات الجديدة ---
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_channel_id = State()
    waiting_for_channel_link = State()
    waiting_for_start_msg = State()
    waiting_for_reply_msg = State()
    # حالات إضافية للميزات الجديدة
    waiting_for_new_admin_id = State()
    waiting_for_db_import = State()

# --- نظام قاعدة البيانات المتكامل ---
DB_PATH = "full_database.json"

def load_db():
    default = {
        "members": [], 
        "admins": [SUDO_ID], 
        "bans": [],
        "channels": [], # قائمة القنوات: {"id": "@ch", "link": "https://t.me/.."}
        "settings": {
            "tanbih": "on", 
            "estgbal": "on", 
            "start_msg": "مرحباً بك في بوت التواصل الاحترافي.", 
            "reply_msg": "✅ تم استلام رسالتك، سيتم الرد عليك قريباً."
        },
        "protection": {
            "photo": "off", "video": "off", "voice": "off", 
            "forward": "off", "link": "off", "sticker": "off"
        },
        "msg_map": {}, 
        "ticket_count": 1000,
        "stats": {"total_received": 0, "total_sent": 0}
    }
    if not os.path.exists(DB_PATH):
        return default
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

db = load_db()

# --- دوال التحقق المتقدمة ---
async def is_subscribed(user_id):
    if user_id in db["admins"]: return True
    if not db["channels"]: return True # إذا لم تكن هناك قنوات مضافة
    for ch in db["channels"]:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in ["left", "kicked"]: return False
        except: continue
    return True

# --- لوحات التحكم (Admin UI) ---
def get_main_admin_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 القنوات", callback_data="manage_channels"),
                InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="manage_settings"))
    builder.row(InlineKeyboardButton(text="🛡️ الحماية", callback_data="manage_protection"),
                InlineKeyboardButton(text="📊 الإحصائيات", callback_data="view_stats"))
    builder.row(InlineKeyboardButton(text="📻 إذاعة", callback_data="start_broadcast"),
                InlineKeyboardButton(text="🚫 المحظورين", callback_data="view_bans"))
    # الإبقاء على زر النسخ الاحتياطي وإضافة أزرار الإدارة الجديدة كـ Extension
    builder.row(InlineKeyboardButton(text="👤 إدارة الأدمن", callback_data="manage_admins"),
                InlineKeyboardButton(text="💾 نسخة احتياطية", callback_data="backup_db"))
    builder.row(InlineKeyboardButton(text="📤 استيراد قاعدة", callback_data="import_db_start"))
    return builder.as_markup()

# --- معالجة الأوامر ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    u_id = message.from_user.id
    if str(u_id) not in db["members"]:
        db["members"].append(str(u_id))
        save_db(db)
        if db["settings"]["tanbih"] == "on":
            await bot.send_message(SUDO_ID, f"🆕 **عضو جديد:**\nالاسم: {message.from_user.full_name}\nID: `{u_id}`")

    if not await is_subscribed(u_id):
        kb = InlineKeyboardBuilder()
        for ch in db["channels"]:
            kb.row(InlineKeyboardButton(text="اضغط للاشتراك", url=ch["link"]))
        kb.row(InlineKeyboardButton(text="✅ تحقق", callback_data="check_sub"))
        return await message.answer("⚠️ **يجب الاشتراك أولاً لاستخدام البوت:**", reply_markup=kb.as_markup())

    await message.answer(db["settings"]["start_msg"])

@dp.message(F.text == "م", F.from_user.id.in_(db["admins"]))
async def admin_panel(message: types.Message):
    await message.answer("👮 **لوحة تحكم المطور الشاملة:**", reply_markup=get_main_admin_kb())

# --- نظام التواصل الجوهري (The Core) ---
@dp.message(F.chat.type == "private")
async def main_communication(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # حماية: منع تداخل رسائل التواصل مع حالات الإدارة أو الإعدادات للأدمن
    current_state = await state.get_state()
    if user_id in db["admins"] and current_state is not None:
        return

    if user_id in db["bans"]: return

    # حالة الرد من المطور
    if user_id in db["admins"] and message.reply_to_message:
        mapped = db["msg_map"].get(str(message.reply_to_message.message_id))
        if mapped:
            try:
                await bot.send_chat_action(mapped["user_id"], "typing")
                await message.copy_to(chat_id=mapped["user_id"])
                db["stats"]["total_sent"] += 1
                save_db(db)
                return await message.reply(f"🎯 **تم الرد على التذكرة #{mapped['ticket']}**")
            except TelegramForbiddenError:
                return await message.reply("❌ حظرك العضو.")
        return

    # استقبال رسائل المستخدمين
    if db["settings"]["estgbal"] == "off":
        return await message.answer("⚠️ الاستقبال معطل حالياً.")

    # تطبيق فلاتر الحماية
    p = db["protection"]
    if (p["photo"] == "on" and message.photo) or \
       (p["video"] == "on" and message.video) or \
       (p["forward"] == "on" and message.forward_from) or \
       (p["link"] == "on" and "t.me" in (message.text or "")):
        return await message.answer("🚫 عذراً، هذا النوع من الرسائل محظور حالياً.")

    # إنشاء التذكرة وإرسالها
    db["ticket_count"] += 1
    t_id = db["ticket_count"]
    
    ban_kb = InlineKeyboardBuilder()
    ban_kb.add(InlineKeyboardButton(text="🚫 حظر", callback_data=f"ban_{user_id}"))
    
    header = f"📩 **تذكرة جديدة #{t_id}**\n👤: {message.from_user.full_name}\n🆔: `{user_id}`"
    await bot.send_message(SUDO_ID, header)
    
    try:
        fwd = await message.forward(chat_id=SUDO_ID)
        db["msg_map"][str(fwd.message_id)] = {"user_id": user_id, "ticket": t_id}
        db["stats"]["total_received"] += 1
        save_db(db)
        
        await bot.send_message(SUDO_ID, "👆 استخدم الرد السريع للإجابة:", reply_markup=ban_kb.as_markup())
        await message.answer(db["settings"]["reply_msg"] + f"\n🎫 رقم التذكرة: `#{t_id}`")
    except Exception as e:
        logging.error(e)

# --- أنظمة التحكم (Callbacks & Operations) ---

@dp.callback_query(F.data == "view_stats")
async def stats_cb(call: types.Callback_query):
    s = db["stats"]
    text = f"📊 **إحصائيات النظام:**\n\n" \
           f"👥 المشتركين: {len(db['members'])}\n" \
           f"📩 رسائل مستلمة: {s['total_received']}\n" \
           f"📤 ردود مرسلة: {s['total_sent']}\n" \
           f"🚫 المحظورين: {len(db['bans'])}"
    await call.message.edit_text(text, reply_markup=get_main_admin_kb())

@dp.callback_query(F.data == "manage_protection")
async def prot_cb(call: types.Callback_query):
    builder = InlineKeyboardBuilder()
    for k, v in db["protection"].items():
        status = "✅" if v == "on" else "❌"
        builder.row(InlineKeyboardButton(text=f"{k}: {status}", callback_data=f"toggle_{k}"))
    builder.row(InlineKeyboardButton(text="↩️ رجوع", callback_data="back_admin"))
    await call.message.edit_text("🛡️ **إعدادات الحماية:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_logic(call: types.Callback_query):
    key = call.data.split("_")[1]
    db["protection"][key] = "on" if db["protection"][key] == "off" else "off"
    save_db(db)
    await prot_cb(call)

@dp.callback_query(F.data.startswith("ban_"))
async def ban_user_cb(call: types.Callback_query):
    u_id = int(call.data.split("_")[1])
    if u_id not in db["bans"]:
        db["bans"].append(u_id)
        save_db(db)
        await call.answer("✅ تم الحظر", show_alert=True)
        await call.message.edit_text(call.message.text + "\n\n🚫 **هذا المستخدم محظور حالياً.**")

@dp.callback_query(F.data == "start_broadcast")
async def broadcast_ui(call: types.Callback_query, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast)
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="❌ إلغاء الإذاعة", callback_data="back_admin"))
    await call.message.edit_text("📣 **أرسل الآن الرسالة التي تريد إذاعتها (نص، صورة، فيديو، إلخ):**", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "back_admin")
async def back_admin(call: types.Callback_query, state: FSMContext):
    await state.clear()
    await call.message.edit_text("👮 **لوحة تحكم المطور الشاملة:**", reply_markup=get_main_admin_kb())

# --- [Extension] نظام الإذاعة المتطور مع العداد و FSM ---

@dp.message(AdminStates.waiting_for_broadcast, F.from_user.id.in_(db["admins"]))
async def extension_broadcast_processor(message: types.Message, state: FSMContext):
    await state.clear()
    users = db["members"]
    success = 0
    failed = 0
    
    progress_msg = await message.answer(f"📡 **جاري بدء الإذاعة لـ {len(users)} عضو...**")
    
    for user_id in users:
        try:
            await message.copy_to(chat_id=int(user_id))
            success += 1
            if success % 10 == 0:
                await progress_msg.edit_text(f"📡 **جاري الإرسال...**\n✅ تم: `{success}`\n❌ فشل: `{failed}`")
        except TelegramForbiddenError:
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    final_text = (
        f"📢 **اكتملت عملية الإذاعة بنجاح!**\n\n"
        f"✅ وصلت لـ: `{success}` عضو\n"
        f"❌ فشل الإرسال لـ: `{failed}` عضو\n"
        f"📊 المجموع الكلي: `{len(users)}`"
    )
    await progress_msg.edit_text(final_text, reply_markup=get_main_admin_kb())

# --- [Extension] إدارة القنوات وفك الحظر والنسخ الاحتياطي ---

@dp.callback_query(F.data == "manage_channels")
async def manage_channels_ui(call: types.Callback_query):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ إضافة قناة", callback_data="add_channel"))
    builder.row(InlineKeyboardButton(text="🗑️ مسح جميع القنوات", callback_data="clear_channels"))
    builder.row(InlineKeyboardButton(text="↩️ رجوع", callback_data="back_admin"))
    
    ch_list = "\n".join([f"🔗 {c['id']}" for c in db["channels"]]) if db["channels"] else "لا توجد قنوات."
    await call.message.edit_text(f"📢 **إدارة القنوات الحالية:**\n\n{ch_list}", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_channel")
async def add_channel_start(call: types.Callback_query, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_channel_id)
    await call.message.edit_text("ارسل معرف القناة (مثال: @YourChannel):")

@dp.message(AdminStates.waiting_for_channel_id, F.from_user.id.in_(db["admins"]))
async def add_channel_id(message: types.Message, state: FSMContext):
    await state.update_data(ch_id=message.text)
    await state.set_state(AdminStates.waiting_for_channel_link)
    await message.answer("ارسل رابط القناة (https://t.me/...):")

@dp.message(AdminStates.waiting_for_channel_link, F.from_user.id.in_(db["admins"]))
async def add_channel_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db["channels"].append({"id": data['ch_id'], "link": message.text})
    save_db(db)
    await state.clear()
    await message.answer("✅ تم إضافة القناة بنجاح!", reply_markup=get_main_admin_kb())

@dp.callback_query(F.data == "view_bans")
async def view_bans_ui(call: types.Callback_query):
    builder = InlineKeyboardBuilder()
    if db["bans"]:
        for user_id in db["bans"]:
            builder.row(InlineKeyboardButton(text=f"فك حظر {user_id}", callback_data=f"unban_{user_id}"))
    builder.row(InlineKeyboardButton(text="↩️ رجوع", callback_data="back_admin"))
    await call.message.edit_text(f"🚫 **قائمة المحظورين ({len(db['bans'])}):**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("unban_"))
async def unban_user_cb(call: types.Callback_query):
    u_id = int(call.data.split("_")[1])
    if u_id in db["bans"]:
        db["bans"].remove(u_id)
        save_db(db)
        await call.answer("✅ تم فك الحظر بنجاح.", show_alert=True)
        await view_bans_ui(call)

@dp.callback_query(F.data == "backup_db")
async def backup_db_cb(call: types.Callback_query):
    try:
        doc = types.FSInputFile(DB_PATH)
        await bot.send_document(call.from_user.id, doc, caption=f"💾 نسخة احتياطية للقاعدة\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        await call.answer("✅ تم إرسال النسخة الاحتياطية.")
    except Exception as e:
        await call.answer(f"❌ خطأ: {str(e)}")

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(call: types.Callback_query):
    if await is_subscribed(call.from_user.id):
        await call.message.edit_text("✅ تم التحقق، يمكنك الآن استخدام البوت.")
    else:
        await call.answer("❌ لم تشترك في القنوات بعد!", show_alert=True)

# --- [Extension Layer] نظام إدارة الأدمن واستيراد القاعدة ---

@dp.callback_query(F.data == "manage_admins")
async def manage_admins_ui(call: types.Callback_query):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ إضافة أدمن", callback_data="add_new_admin"))
    
    admin_list = "👥 **قائمة الأدمن الحالية:**\n\n"
    for adm in db["admins"]:
        status = "👑 (SUDO)" if adm == SUDO_ID else ""
        admin_list += f"• `{adm}` {status}\n"
        if adm != SUDO_ID:
            builder.row(InlineKeyboardButton(text=f"🗑️ حذف {adm}", callback_data=f"rem_admin_{adm}"))
            
    builder.row(InlineKeyboardButton(text="↩️ رجوع", callback_data="back_admin"))
    await call.message.edit_text(admin_list, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_new_admin")
async def add_admin_start(call: types.Callback_query, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_new_admin_id)
    await call.message.edit_text("ارسل ايدي الأدمن الجديد (رقم فقط):")

@dp.message(AdminStates.waiting_for_new_admin_id, F.from_user.id == SUDO_ID)
async def add_admin_id(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        new_adm = int(message.text)
        if new_adm not in db["admins"]:
            db["admins"].append(new_adm)
            save_db(db)
            await message.answer(f"✅ تم إضافة `{new_adm}` كأدمن بنجاح.")
        else:
            await message.answer("⚠️ هذا الأيدي موجود بالفعل كأدمن.")
    else:
        await message.answer("❌ يرجى إرسال رقم صحيح.")
    await state.clear()
    await admin_panel(message)

@dp.callback_query(F.data.startswith("rem_admin_"))
async def remove_admin_cb(call: types.Callback_query):
    adm_id = int(call.data.split("_")[2])
    if adm_id != SUDO_ID:
        db["admins"].remove(adm_id)
        save_db(db)
        await call.answer(f"✅ تم حذف الأدمن {adm_id}", show_alert=True)
        await manage_admins_ui(call)
    else:
        await call.answer("❌ لا يمكن حذف المطور الأساسي!", show_alert=True)

@dp.callback_query(F.data == "import_db_start")
async def import_db_start_cb(call: types.Callback_query, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_db_import)
    await call.message.edit_text("📤 **يرجى إرسال ملف (JSON) لاستيراد قاعدة البيانات:**\n⚠️ تنبيه: سيتم استبدال القاعدة الحالية بالكامل!")

@dp.message(AdminStates.waiting_for_db_import, F.document, F.from_user.id == SUDO_ID)
async def import_db_process(message: types.Message, state: FSMContext):
    if not message.document.file_name.endswith('.json'):
        return await message.answer("❌ خطأ: يرجى إرسال ملف بصيغة .json فقط.")

    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    
    # تحميل الملف من سيرفرات تلجرام
    content = await bot.download_file(file_path)
    try:
        new_db = json.load(content)
        # التحقق من وجود المفاتيح الأساسية لضمان سلامة القاعدة
        required_keys = ["members", "admins", "bans", "channels", "settings", "protection", "msg_map", "ticket_count", "stats"]
        if all(key in new_db for key in required_keys):
            # التأكد من بقاء المطور الأساسي في القائمة المستوردة
            if SUDO_ID not in new_db["admins"]:
                new_db["admins"].append(SUDO_ID)
            
            # استبدال القاعدة الحالية
            global db
            db = new_db
            save_db(db)
            await message.answer("✅ تم استيراد قاعدة البيانات بنجاح وتحديث النظام!")
        else:
            await message.answer("❌ خطأ: الملف لا يحتوي على هيكل القاعدة الصحيح.")
    except Exception as e:
        await message.answer(f"❌ خطأ أثناء القراءة: {str(e)}")
    
    await state.clear()
    await admin_panel(message)

# --- التشغيل ---
async def main():
    print(f"🚀 [System Online] - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    # مهمة دورية: نسخ احتياطي تلقائي كل 12 ساعة كملحق خلفي
    asyncio.create_task(auto_backup_task())
    await dp.start_polling(bot)

async def auto_backup_task():
    while True:
        await asyncio.sleep(43200) # 12 ساعة
        try:
            doc = types.FSInputFile(DB_PATH)
            await bot.send_document(SUDO_ID, doc, caption="📦 نظام النسخ الاحتياطي التلقائي (كل 12 ساعة)")
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
