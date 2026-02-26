#!/usr/bin/env python3
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from config import (
    BOT_TOKEN,
    ADMIN_IDS,
    DOCUMENTS_PATH,
    REMINDER_CHECK_INTERVAL_SECONDS,
    REMIND_BEFORE_MINUTES,
    ENABLE_PRIORITY_MATCHING,
    ENABLE_SURGE_PRICING,
    PRIORITY_FEE,
)
import database as db
import matching
import analytics as an

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

os.makedirs(DOCUMENTS_PATH, exist_ok=True)

(
    MAIN_MENU,
    REG_ROLE, REG_NAME, REG_PHONE, REG_DOCUMENT_TYPE, REG_DOCUMENT,
    REG_VEHICLE_TYPE, REG_VEHICLE_SEATS, REG_VEHICLE_YEAR,
    RIDER_PICKUP, RIDER_DROPOFF, RIDER_TIME, RIDER_PASSENGERS, RIDER_PRIORITY, RIDER_CONFIRM_FARE,
    DRIVER_START, DRIVER_END, DRIVER_TIME, DRIVER_SEATS,
    HISTORY_MENU,
    COMPLETE_CONFIRM,
    RATE_SELECT, RATE_STARS, RATE_COMMENT,
    ADMIN_MENU, ADMIN_VIEW_USER
) = range(26)

TIME_SLOTS = [
    "06:00", "06:30", "07:00", "07:30", "08:00", "08:30",
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
    "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
    "18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
    "21:00", "21:30", "22:00", "22:30", "23:00",
]

VEHICLE_TYPES = ["Car", "SUV", "Van", "Minibus"]
DOCUMENT_TYPES = ["ID Card", "Driving License", "Passport"]


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user = db.get_user(user_id)
    keyboard: List[List[InlineKeyboardButton]] = []

    if not user:
        keyboard.append([InlineKeyboardButton("📝 Register", callback_data="register")])
    elif user["status"] == "pending":
        keyboard.append([InlineKeyboardButton("⏳ Registration Pending", callback_data="status_pending")])
    elif user["status"] == "rejected":
        keyboard.append([InlineKeyboardButton("❌ Rejected - Re-register", callback_data="register")])
    elif user["status"] == "approved":
        if user["role"] == "passenger":
            keyboard.append([InlineKeyboardButton("🚗 Request Ride", callback_data="request_ride")])
        elif user["role"] == "driver":
            keyboard.append([InlineKeyboardButton("🚙 Offer Ride", callback_data="offer_ride")])

        keyboard.append([InlineKeyboardButton("❌ Cancel Active", callback_data="cancel_active")])
        keyboard.append([InlineKeyboardButton("📜 Ride History", callback_data="history")])
        keyboard.append([InlineKeyboardButton("👤 My Profile", callback_data="my_profile")])

        if user["role"] == "driver":
            trip = db.get_active_trip_for_driver(user_id)
            if trip:
                keyboard.append([InlineKeyboardButton("✅ Complete Active Trip", callback_data="complete_trip")])

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🔧 Admin Panel", callback_data="admin_panel")])

    return InlineKeyboardMarkup(keyboard)


def get_registration_role_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚗 Register as Passenger", callback_data="reg_passenger")],
        [InlineKeyboardButton("🚙 Register as Driver", callback_data="reg_driver")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_document_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(doc, callback_data=f"doctype_{doc}")] for doc in DOCUMENT_TYPES]
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_vehicle_type_keyboard() -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for vtype in VEHICLE_TYPES:
        row.append(InlineKeyboardButton(vtype, callback_data=f"vtype_{vtype}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_vehicle_seats_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("2", callback_data="vseats_2"),
            InlineKeyboardButton("3", callback_data="vseats_3"),
            InlineKeyboardButton("4", callback_data="vseats_4"),
        ],
        [
            InlineKeyboardButton("5", callback_data="vseats_5"),
            InlineKeyboardButton("6", callback_data="vseats_6"),
            InlineKeyboardButton("7+", callback_data="vseats_7"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_location_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("📍 Share Current Location", request_location=True), KeyboardButton("📍 Choose On Map")]
    ]
    return ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)


async def rider_pickup_choose_on_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "To choose a pickup point: attachment → Location → drop pin → Send Location.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return RIDER_PICKUP


async def rider_dropoff_choose_on_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "To choose a drop-off point: attachment → Location → drop pin → Send Location.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return RIDER_DROPOFF


async def driver_start_choose_on_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "To choose your start point: attachment → Location → drop pin → Send Location.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DRIVER_START


async def driver_end_choose_on_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "To choose your destination: attachment → Location → drop pin → Send Location.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DRIVER_END


def get_time_keyboard() -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for time in TIME_SLOTS:
        row.append(InlineKeyboardButton(time, callback_data=f"time_{time}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_passenger_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="passengers_1"),
            InlineKeyboardButton("2", callback_data="passengers_2"),
            InlineKeyboardButton("3", callback_data="passengers_3"),
            InlineKeyboardButton("4", callback_data="passengers_4"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_priority_keyboard() -> InlineKeyboardMarkup:
    if not ENABLE_PRIORITY_MATCHING:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Continue", callback_data="priority_no")]])

    keyboard = [
        [InlineKeyboardButton(f"⚡ Priority (adds {matching.format_money(PRIORITY_FEE)})", callback_data="priority_yes")],
        [InlineKeyboardButton("Standard", callback_data="priority_no")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_seats_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="seats_1"),
            InlineKeyboardButton("2", callback_data="seats_2"),
            InlineKeyboardButton("3", callback_data="seats_3"),
            InlineKeyboardButton("4", callback_data="seats_4"),
        ],
        [
            InlineKeyboardButton("5", callback_data="seats_5"),
            InlineKeyboardButton("6", callback_data="seats_6"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📋 Pending Registrations", callback_data="admin_pending")],
        [InlineKeyboardButton("📊 Registration Report", callback_data="admin_report_reg")],
        [InlineKeyboardButton("🚗 Trip Report", callback_data="admin_report_trips")],
        [InlineKeyboardButton("⏱️ Waiting Time Report", callback_data="admin_report_wait")],
        [InlineKeyboardButton("💺 Seat Utilization Report", callback_data="admin_report_seats")],
        [InlineKeyboardButton("📈 Advanced Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("👥 All Users", callback_data="admin_users")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def rating_stars_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("⭐" * i, callback_data=f"rate_{i}")] for i in range(1, 6)]
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update.message.reply_text(
        f"Welcome to RideShare Bot, {user.first_name}! 🚗\n\nChoose an option below:",
        reply_markup=get_main_menu_keyboard(user.id),
    )
    return MAIN_MENU


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Your Telegram User ID: `{user.id}`\n\n"
        "Add this ID into ADMIN_IDS (env var) to grant admin access.",
        parse_mode="Markdown",
    )


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "register":
        await query.edit_message_text(
            "📝 *Registration*\n\nPlease select your role:",
            parse_mode="Markdown",
            reply_markup=get_registration_role_keyboard(),
        )
        return REG_ROLE

    if query.data == "status_pending":
        await query.edit_message_text(
            "⏳ Your registration is pending approval.\n\nYou'll be notified when approved.",
            reply_markup=get_main_menu_keyboard(user.id),
        )
        return MAIN_MENU

    if query.data == "my_profile":
        user_data = db.get_user(user.id)
        if user_data:
            profile_text = (
                f"👤 *Your Profile*\n\n"
                f"*Name:* {user_data['name']}\n"
                f"*Phone:* {user_data['phone']}\n"
                f"*Role:* {user_data['role'].title()}\n"
                f"*Status:* {user_data['status'].title()}\n"
            )
            if user_data["role"] == "driver":
                profile_text += (
                    f"\n*Vehicle:* {user_data.get('vehicle_type')}\n"
                    f"*Seats:* {user_data.get('vehicle_seats')}\n"
                    f"*Year/Model:* {user_data.get('vehicle_year_model')}\n"
                )
            await query.edit_message_text(
                profile_text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user.id)
            )
        return MAIN_MENU

    if query.data == "history":
        if not db.is_user_approved(user.id):
            await query.edit_message_text(
                "❌ You need to be registered and approved to view history.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU
        return await history_menu(update, context)

    if query.data == "request_ride":
        if not db.is_user_approved(user.id):
            await query.edit_message_text(
                "❌ You need to be registered and approved to request rides.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU

        # ✅ BLOCK new requests while existing searching request exists
        if db.has_active_rider_request(user.id):
            await query.edit_message_text(
                "⏳ You already have an active ride request.\n\n"
                "Please wait until it is matched or expires, or cancel it from the main menu.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU

        context.user_data.clear()
        context.user_data["flow"] = "rider"
        await query.edit_message_text(
            "🚗 *Request a Ride*\n\nStep 1/5: Please share your *pickup location*.",
            parse_mode="Markdown",
        )
        await query.message.reply_text("📍 Tap to share your pickup location:", reply_markup=get_location_keyboard())
        return RIDER_PICKUP

    if query.data == "offer_ride":
        user_data = db.get_user(user.id)
        if not user_data or user_data["status"] != "approved" or user_data["role"] != "driver":
            await query.edit_message_text(
                "❌ You need to be registered and approved as a driver to offer rides.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU

        # ✅ BLOCK new offers while existing available offer exists
        if db.has_active_driver_offer(user.id):
            await query.edit_message_text(
                "⏳ You already have an active ride offer.\n\n"
                "Please wait until it is matched or expires, or cancel it from the main menu.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU

        context.user_data.clear()
        context.user_data["flow"] = "driver"
        await query.edit_message_text(
            "🚙 *Offer a Ride*\n\nStep 1/4: Please share your *starting location*.",
            parse_mode="Markdown",
        )
        await query.message.reply_text("📍 Tap to share your start location:", reply_markup=get_location_keyboard())
        return DRIVER_START

    if query.data == "cancel_active":
        rider_cancelled = db.cancel_rider_request(user.id)
        driver_cancelled = db.cancel_driver_offer(user.id)
        msg = "✅ Your active request/offer has been cancelled." if (rider_cancelled or driver_cancelled) else "ℹ️ No active request/offer found."
        await query.edit_message_text(msg + "\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if query.data == "admin_panel":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Access denied.", reply_markup=get_main_menu_keyboard(user.id))
            return MAIN_MENU

        await query.edit_message_text(
            "🔧 *Admin Panel*\n\nSelect an option:",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(),
        )
        return ADMIN_MENU

    if query.data == "back_main":
        await query.edit_message_text("Choose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    return MAIN_MENU


# ---------------- registration (same as your original) ----------------
async def reg_role_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Registration cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data == "reg_passenger":
        context.user_data["reg_role"] = "passenger"
    elif query.data == "reg_driver":
        context.user_data["reg_role"] = "driver"
    else:
        return REG_ROLE

    await query.edit_message_text("📝 *Registration*\n\nPlease enter your *full name*:", parse_mode="Markdown")
    return REG_NAME


async def reg_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg_name"] = update.message.text.strip()
    await update.message.reply_text(
        "📱 Please enter your *phone number*:\n\n(Include country code, e.g., +1234567890)",
        parse_mode="Markdown",
    )
    return REG_PHONE


async def reg_phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg_phone"] = update.message.text.strip()
    await update.message.reply_text(
        "📄 Please select your *document type* for verification:",
        parse_mode="Markdown",
        reply_markup=get_document_type_keyboard(),
    )
    return REG_DOCUMENT_TYPE


async def reg_document_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Registration cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data.startswith("doctype_"):
        context.user_data["reg_doc_type"] = query.data.replace("doctype_", "")
        await query.edit_message_text(
            f"📄 Document type: *{context.user_data['reg_doc_type']}*\n\nPlease send a *photo* of your document:",
            parse_mode="Markdown",
        )
        return REG_DOCUMENT

    return REG_DOCUMENT_TYPE


async def reg_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user

    if not update.message.photo:
        await update.message.reply_text("Please send a *photo* of your document.", parse_mode="Markdown")
        return REG_DOCUMENT

    photo = update.message.photo[-1]
    file = await photo.get_file()

    doc_filename = f"{user.id}_{context.user_data['reg_doc_type'].replace(' ', '_')}.jpg"
    doc_path = os.path.join(DOCUMENTS_PATH, doc_filename)
    await file.download_to_drive(doc_path)
    context.user_data["reg_doc_path"] = doc_path

    if context.user_data["reg_role"] == "driver":
        await update.message.reply_text(
            "✅ Document saved!\n\nNow select your *vehicle type*:",
            parse_mode="Markdown",
            reply_markup=get_vehicle_type_keyboard(),
        )
        return REG_VEHICLE_TYPE

    return await complete_registration(update, context)


async def reg_vehicle_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Registration cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data.startswith("vtype_"):
        context.user_data["reg_vehicle_type"] = query.data.replace("vtype_", "")
        await query.edit_message_text(
            f"🚗 Vehicle type: *{context.user_data['reg_vehicle_type']}*\n\nHow many *passenger seats*?",
            parse_mode="Markdown",
            reply_markup=get_vehicle_seats_keyboard(),
        )
        return REG_VEHICLE_SEATS

    return REG_VEHICLE_TYPE


async def reg_vehicle_seats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Registration cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data.startswith("vseats_"):
        context.user_data["reg_vehicle_seats"] = int(query.data.replace("vseats_", ""))
        await query.edit_message_text(
            "📅 Please enter your vehicle *year and model* (e.g., 2020 Toyota Camry):",
            parse_mode="Markdown",
        )
        return REG_VEHICLE_YEAR

    return REG_VEHICLE_SEATS


async def reg_vehicle_year_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg_vehicle_year"] = update.message.text.strip()
    return await complete_registration(update, context)


async def complete_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    db.register_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        role=context.user_data["reg_role"],
        name=context.user_data["reg_name"],
        phone=context.user_data["reg_phone"],
        document_type=context.user_data.get("reg_doc_type"),
        document_path=context.user_data.get("reg_doc_path"),
        vehicle_type=context.user_data.get("reg_vehicle_type"),
        vehicle_seats=context.user_data.get("reg_vehicle_seats"),
        vehicle_year_model=context.user_data.get("reg_vehicle_year"),
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🆕 *New Registration Request*\n\n"
                    f"Name: {context.user_data['reg_name']}\n"
                    f"Role: {context.user_data['reg_role'].title()}\n"
                    f"Phone: {context.user_data['reg_phone']}\n\n"
                    "Use Admin Panel to review."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to notify admin %s: %s", admin_id, e)

    msg = (
        "✅ *Registration Submitted!*\n\n"
        "Your application is pending admin approval.\n"
        "You'll be notified once approved."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user.id))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user.id))

    context.user_data.clear()
    return MAIN_MENU


# ---------------- rider flow ----------------
async def rider_pickup_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Please share your location using the button below.", reply_markup=get_location_keyboard())
        return RIDER_PICKUP

    context.user_data["pickup_lat"] = update.message.location.latitude
    context.user_data["pickup_lon"] = update.message.location.longitude

    await update.message.reply_text(
        "✅ Pickup location saved!\n\nStep 2/5: Share your *drop-off location*.",
        parse_mode="Markdown",
        reply_markup=get_location_keyboard(),
    )
    return RIDER_DROPOFF


async def rider_dropoff_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Please share your location using the button below.", reply_markup=get_location_keyboard())
        return RIDER_DROPOFF

    context.user_data["dropoff_lat"] = update.message.location.latitude
    context.user_data["dropoff_lon"] = update.message.location.longitude

    await update.message.reply_text(
        "✅ Drop-off location saved!\n\nStep 3/5: Select your *preferred ride time*:",
        parse_mode="Markdown",
        reply_markup=get_time_keyboard(),
    )
    return RIDER_TIME


async def rider_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Request cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data.startswith("time_"):
        context.user_data["ride_time"] = query.data.replace("time_", "")
        await query.edit_message_text(
            f"✅ Time set to {context.user_data['ride_time']}\n\nStep 4/5: How many *passengers* (including you)?",
            parse_mode="Markdown",
            reply_markup=get_passenger_keyboard(),
        )
        return RIDER_PASSENGERS

    return RIDER_TIME


async def rider_passengers_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "cancel":
        await query.edit_message_text("❌ Request cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if not query.data.startswith("passengers_"):
        return RIDER_PASSENGERS

    passengers = int(query.data.replace("passengers_", ""))
    context.user_data["passengers"] = passengers

    await query.edit_message_text(
        "Step 5/5: Choose *Priority* or Standard.\n\nPriority requests are matched first.",
        parse_mode="Markdown",
        reply_markup=get_priority_keyboard(),
    )
    return RIDER_PRIORITY


async def rider_priority_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "cancel":
        await query.edit_message_text("❌ Request cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    is_priority = (query.data == "priority_yes") if ENABLE_PRIORITY_MATCHING else False
    context.user_data["is_priority"] = 1 if is_priority else 0

    fare_info = matching.get_fare_estimate(
        context.user_data["pickup_lat"],
        context.user_data["pickup_lon"],
        context.user_data["dropoff_lat"],
        context.user_data["dropoff_lon"],
        context.user_data["passengers"],
        ride_time=context.user_data["ride_time"] if ENABLE_SURGE_PRICING else None,
    )
    context.user_data["fare_info"] = fare_info

    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Request Ride", callback_data="confirm_ride")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]

    await query.edit_message_text(
        "💰 *Fare Estimate*\n\n"
        f"📏 Distance: {fare_info['distance_km']} km\n"
        f"🕐 Time: {context.user_data['ride_time']}\n"
        f"👥 Passengers: {context.user_data['passengers']}\n\n"
        f"💵 *Total Fare:* {fare_info['formatted_total']}\n"
        f"👤 *Per Person:* {fare_info['formatted_per_passenger']}\n\n"
        "Confirm to submit your ride request?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RIDER_CONFIRM_FARE


async def rider_confirm_fare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "cancel":
        await query.edit_message_text("❌ Request cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if query.data != "confirm_ride":
        return RIDER_CONFIRM_FARE

    passengers = int(context.user_data["passengers"])
    fare_info = context.user_data["fare_info"]
    is_priority = int(context.user_data.get("is_priority", 0))

    try:
        db.add_rider(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "Rider",
            pickup_lat=context.user_data["pickup_lat"],
            pickup_lon=context.user_data["pickup_lon"],
            dropoff_lat=context.user_data["dropoff_lat"],
            dropoff_lon=context.user_data["dropoff_lon"],
            ride_time=context.user_data["ride_time"],
            passengers=passengers,
            is_priority=is_priority,
            surge_multiplier=float(fare_info.get("surge_multiplier", 1.0)),
            fare_total=float(fare_info["total_fare"]),
        )
    except ValueError:
        await query.edit_message_text(
            "⏳ You already have an active ride request.\n\n"
            "Please wait until it is matched or expires, or cancel it from the main menu.",
            reply_markup=get_main_menu_keyboard(user.id),
        )
        context.user_data.clear()
        return MAIN_MENU

    await query.edit_message_text(
        "✅ *Ride Request Submitted!*\n\n"
        f"🕐 Time: {context.user_data['ride_time']}\n"
        f"👥 Passengers: {passengers}\n"
        f"💰 Estimated fare: {fare_info['formatted_per_passenger']} per person\n\n"
        "🔍 *Looking for a matching driver...*",
        parse_mode="Markdown",
    )

    rider = db.get_rider_by_user_id(user.id)
    if rider:
        match = matching.find_match_for_rider(rider)
        if match:
            details = matching.process_match(rider, match)
            await notify_match(context, details)

    await query.message.reply_text("What would you like to do next?", reply_markup=get_main_menu_keyboard(user.id))
    context.user_data.clear()
    return MAIN_MENU


# ---------------- driver flow ----------------
async def driver_start_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Please share your location using the button below.", reply_markup=get_location_keyboard())
        return DRIVER_START

    context.user_data["start_lat"] = update.message.location.latitude
    context.user_data["start_lon"] = update.message.location.longitude
    await update.message.reply_text(
        "✅ Start saved!\n\nStep 2/4: Share your *destination/end location*.",
        parse_mode="Markdown",
        reply_markup=get_location_keyboard(),
    )
    return DRIVER_END


async def driver_end_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Please share your location using the button below.", reply_markup=get_location_keyboard())
        return DRIVER_END

    context.user_data["end_lat"] = update.message.location.latitude
    context.user_data["end_lon"] = update.message.location.longitude
    await update.message.reply_text(
        "✅ Destination saved!\n\nStep 3/4: Select your *departure time*:",
        parse_mode="Markdown",
        reply_markup=get_time_keyboard(),
    )
    return DRIVER_TIME


async def driver_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Offer cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(update.effective_user.id))
        return MAIN_MENU

    if query.data.startswith("time_"):
        context.user_data["ride_time"] = query.data.replace("time_", "")
        await query.edit_message_text(
            f"✅ Departure set to {context.user_data['ride_time']}\n\nStep 4/4: How many *seats available*?",
            parse_mode="Markdown",
            reply_markup=get_seats_keyboard(),
        )
        return DRIVER_SEATS

    return DRIVER_TIME


async def driver_seats_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "cancel":
        await query.edit_message_text("❌ Offer cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if not query.data.startswith("seats_"):
        return DRIVER_SEATS

    seats = int(query.data.replace("seats_", ""))
    context.user_data["available_seats"] = seats

    try:
        db.add_driver(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "Driver",
            start_lat=context.user_data["start_lat"],
            start_lon=context.user_data["start_lon"],
            end_lat=context.user_data["end_lat"],
            end_lon=context.user_data["end_lon"],
            ride_time=context.user_data["ride_time"],
            available_seats=seats,
        )
    except ValueError:
        await query.edit_message_text(
            "⏳ You already have an active ride offer.\n\n"
            "Please wait until it is matched or expires, or cancel it from the main menu.",
            reply_markup=get_main_menu_keyboard(user.id),
        )
        context.user_data.clear()
        return MAIN_MENU

    await query.edit_message_text(
        "✅ *Ride Offer Submitted!*\n\n"
        f"🕐 Departure: {context.user_data['ride_time']}\n"
        f"💺 Seats available: {seats}\n\n"
        "🔍 *Looking for matching riders...*",
        parse_mode="Markdown",
    )

    driver = db.get_driver_by_user_id(user.id)
    if driver:
        match = matching.find_match_for_driver(driver)
        if match:
            details = matching.process_match(match, driver)
            await notify_match(context, details)

    await query.message.reply_text("What would you like to do next?", reply_markup=get_main_menu_keyboard(user.id))
    context.user_data.clear()
    return MAIN_MENU


# ---------------- matching notifications (same logic as original, trimmed) ----------------
async def notify_match(context: ContextTypes.DEFAULT_TYPE, match_details: Dict[str, Any]):
    rider_user_id = match_details["rider_user_id"]
    driver_user_id = match_details["driver_user_id"]
    trip_id = match_details.get("trip_id")

    rider_info = db.get_user(rider_user_id)
    driver_info = db.get_user(driver_user_id)

    rider_name = (rider_info["name"] if rider_info else match_details["rider_first_name"])
    driver_name = (driver_info["name"] if driver_info else match_details["driver_first_name"])

    rider_phone = (rider_info["phone"] if rider_info else "N/A")
    driver_phone = (driver_info["phone"] if driver_info else "N/A")

    rider_contact = f"@{match_details['rider_username']}" if match_details["rider_username"] else "No username"
    driver_contact = f"@{match_details['driver_username']}" if match_details["driver_username"] else "No username"

    fare = match_details.get("fare", {})
    fare_total = fare.get("formatted_total", "N/A")
    fare_per_person = fare.get("formatted_per_passenger", "N/A")
    distance_km = fare.get("distance_km", "N/A")

    trip_line = f"\n🧾 Trip ID: {trip_id}" if trip_id else ""

    rider_message = (
        "🎉 *Match Found!*\n"
        f"{trip_line}\n\n"
        "*Your Driver:*\n"
        f"👤 Name: {driver_name}\n"
        f"📱 Phone: {driver_phone}\n"
        f"💬 Telegram: {driver_contact}\n"
        f"🕐 Departure: {match_details['driver_time']}\n\n"
        "💰 *Fare Details:*\n"
        f"📏 Distance: {distance_km} km\n"
        f"💵 Total: {fare_total}\n"
        f"👤 Your share: {fare_per_person}\n"
    )

    driver_message = (
        "🎉 *Match Found!*\n"
        f"{trip_line}\n\n"
        "*Your Rider:*\n"
        f"👤 Name: {rider_name}\n"
        f"📱 Phone: {rider_phone}\n"
        f"💬 Telegram: {rider_contact}\n"
        f"🕐 Requested time: {match_details['rider_time']}\n"
        f"👥 Passengers: {match_details['passengers']}\n\n"
        "💰 *Fare Details:*\n"
        f"📏 Distance: {distance_km} km\n"
        f"💵 Total to collect: {fare_total}\n"
    )

    try:
        await context.bot.send_message(chat_id=rider_user_id, text=rider_message, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(rider_user_id))
    except Exception as e:
        logger.error("Failed to notify rider %s: %s", rider_user_id, e)

    try:
        await context.bot.send_message(chat_id=driver_user_id, text=driver_message, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(driver_user_id))
    except Exception as e:
        logger.error("Failed to notify driver %s: %s", driver_user_id, e)

    try:
        await context.bot.send_message(chat_id=driver_user_id, text="📍 *Rider's Pickup Location:*", parse_mode="Markdown")
        await context.bot.send_location(chat_id=driver_user_id, latitude=match_details["rider_pickup"][0], longitude=match_details["rider_pickup"][1])
        await context.bot.send_message(chat_id=driver_user_id, text="📍 *Rider's Drop-off Location:*", parse_mode="Markdown")
        await context.bot.send_location(chat_id=driver_user_id, latitude=match_details["rider_dropoff"][0], longitude=match_details["rider_dropoff"][1])
    except Exception as e:
        logger.error("Failed to send locations to driver: %s", e)

    try:
        if REMIND_BEFORE_MINUTES > 0:
            depart_dt = matching.parse_time(match_details["driver_time"])
            remind_dt = depart_dt - timedelta(minutes=REMIND_BEFORE_MINUTES)
            now = datetime.now()
            if remind_dt > now and context.job_queue:
                context.job_queue.run_once(
                    departure_reminder_job,
                    when=(remind_dt - now),
                    data={"user_ids": [rider_user_id, driver_user_id], "trip_id": trip_id, "time": match_details["driver_time"]},
                    name=f"depart_reminder_{trip_id}_{rider_user_id}_{driver_user_id}",
                )
    except Exception as e:
        logger.error("Failed to schedule reminders: %s", e)


# ---------------- history (minimal) ----------------
async def history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    keyboard = [
        [InlineKeyboardButton("📜 Recent Trips", callback_data="history_trips")],
        [InlineKeyboardButton("⭐ My Ratings", callback_data="history_ratings")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
    ]
    await query.edit_message_text("📜 *Ride History*\n\nChoose:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return HISTORY_MENU


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "back_main":
        await query.edit_message_text("Choose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if query.data == "history_trips":
        items = db.get_user_trip_history(user.id, limit=10)
        if not items:
            await query.edit_message_text("📜 *Ride History*\n\nNo trips yet.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]))
            return HISTORY_MENU

        lines = ["📜 *Recent Trips* (last 10)\n"]
        for t in items:
            lines.append(f"• Trip #{t['id']} — {t['ride_time']} — {t['status']} — seats {t['seats_filled']}/{t['total_seats']}")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]))
        return HISTORY_MENU

    if query.data == "history_ratings":
        summary = db.get_user_rating_summary(user.id)
        if not summary:
            await query.edit_message_text("⭐ *My Ratings*\n\nNo ratings yet.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]))
            return HISTORY_MENU

        await query.edit_message_text(
            "⭐ *My Ratings*\n\n"
            f"Average: {summary['avg_rating']} / 5\n"
            f"Total ratings: {summary['count']}\n",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]),
        )
        return HISTORY_MENU

    if query.data == "history":
        return await history_menu(update, context)

    return HISTORY_MENU


# ---------------- background jobs ----------------
async def periodic_jobs(context: ContextTypes.DEFAULT_TYPE):
    try:
        # ✅ expire old stuff first (this enables “paused until expires” rule)
        db.expire_old_requests()

        # Auto-match
        riders = db.get_searching_riders_sorted()
        drivers = db.get_available_drivers()
        for rider in riders:
            m = matching.find_match_for_rider(rider, drivers_cache=drivers)
            if m:
                details = matching.process_match(rider, m)
                await notify_match(context, details)
                drivers = db.get_available_drivers()

        # Remind riders near timeout
        soon = db.get_riders_near_timeout(minutes_left=5)
        for r in soon:
            if r.get("reminder_sent"):
                continue
            try:
                await context.bot.send_message(
                    chat_id=r["user_id"],
                    text="⏳ Reminder: you're still waiting for a driver. You can cancel anytime from the main menu.",
                    reply_markup=get_main_menu_keyboard(r["user_id"]),
                )
                db.mark_rider_reminded(r["id"])
            except Exception as e:
                logger.error("Failed to send reminder: %s", e)

    except Exception as e:
        logger.error("periodic_jobs error: %s", e)


async def departure_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    user_ids = data.get("user_ids", [])
    trip_id = data.get("trip_id")
    time_str = data.get("time", "")
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"⏰ Reminder: Trip #{trip_id} departs at {time_str}.",
                reply_markup=get_main_menu_keyboard(uid),
            )
        except Exception as e:
            logger.error("departure reminder failed: %s", e)


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await query.edit_message_text("❌ Cancelled.\n\nChoose an option:", reply_markup=get_main_menu_keyboard(user.id))
    context.user_data.clear()
    return MAIN_MENU


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update.message.reply_text(
        "I didn't understand that. Please use the buttons.\n\nType /start to see the menu.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return MAIN_MENU


def build_application() -> Application:
    db.init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                main_menu_handler,
                pattern=r"^(register|status_pending|request_ride|offer_ride|cancel_active|history|my_profile|admin_panel|back_main)$",
            ),
        ],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_handler)],
            REG_ROLE: [CallbackQueryHandler(reg_role_handler)],
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name_handler)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone_handler)],
            REG_DOCUMENT_TYPE: [CallbackQueryHandler(reg_document_type_handler)],
            REG_DOCUMENT: [MessageHandler(filters.PHOTO, reg_document_handler)],
            REG_VEHICLE_TYPE: [CallbackQueryHandler(reg_vehicle_type_handler)],
            REG_VEHICLE_SEATS: [CallbackQueryHandler(reg_vehicle_seats_handler)],
            REG_VEHICLE_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_vehicle_year_handler)],

            RIDER_PICKUP: [
                MessageHandler(filters.LOCATION, rider_pickup_location),
                MessageHandler(filters.Regex(r'^📍 Choose On Map$'), rider_pickup_choose_on_map),
                CallbackQueryHandler(cancel_handler, pattern="^cancel$"),
            ],
            RIDER_DROPOFF: [
                MessageHandler(filters.LOCATION, rider_dropoff_location),
                MessageHandler(filters.Regex(r'^📍 Choose On Map$'), rider_dropoff_choose_on_map),
                CallbackQueryHandler(cancel_handler, pattern="^cancel$"),
            ],
            RIDER_TIME: [CallbackQueryHandler(rider_time_selection)],
            RIDER_PASSENGERS: [CallbackQueryHandler(rider_passengers_selection)],
            RIDER_PRIORITY: [CallbackQueryHandler(rider_priority_selection)],
            RIDER_CONFIRM_FARE: [CallbackQueryHandler(rider_confirm_fare)],

            DRIVER_START: [
                MessageHandler(filters.LOCATION, driver_start_location),
                MessageHandler(filters.Regex(r'^📍 Choose On Map$'), driver_start_choose_on_map),
                CallbackQueryHandler(cancel_handler, pattern="^cancel$"),
            ],
            DRIVER_END: [
                MessageHandler(filters.LOCATION, driver_end_location),
                MessageHandler(filters.Regex(r'^📍 Choose On Map$'), driver_end_choose_on_map),
                CallbackQueryHandler(cancel_handler, pattern="^cancel$"),
            ],
            DRIVER_TIME: [CallbackQueryHandler(driver_time_selection)],
            DRIVER_SEATS: [CallbackQueryHandler(driver_seats_selection)],

            HISTORY_MENU: [CallbackQueryHandler(history_handler)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(cancel_handler, pattern="^cancel$"),
            MessageHandler(filters.ALL, fallback_handler),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("myid", myid))

    # ✅ IMPORTANT: job_queue requires python-telegram-bot[job-queue]
    if application.job_queue:
        application.job_queue.run_repeating(periodic_jobs, interval=REMINDER_CHECK_INTERVAL_SECONDS, first=10)
    else:
        logger.warning("JobQueue is not available. Install: pip install 'python-telegram-bot[job-queue]'")

    return application


def main():
    app = build_application()
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()