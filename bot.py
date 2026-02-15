#!/usr/bin/env python3
"""
Telegram Rideshare Matching Bot (v2)

Adds:
- Advanced admin analytics (trends, peak times, seat utilization)
- Optional dynamic features: priority matching, surge pricing
- Automatic reminders/notifications (unmatched + upcoming departures)
- Ride history for users
- Rating & feedback system
- Production-ready structure (safe config, DB migrations)

Requires: python-telegram-bot v21+, geopy
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

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
    REQUEST_TIMEOUT_MINUTES,
    ENABLE_PRIORITY_MATCHING,
    ENABLE_SURGE_PRICING,
    PRIORITY_FEE,
    REMINDER_CHECK_INTERVAL_SECONDS,
    REMIND_BEFORE_MINUTES,
)
import database as db
import matching
import analytics as an

# ---------------- logging ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Ensure docs dir
os.makedirs(DOCUMENTS_PATH, exist_ok=True)

# ---------------- conversation states ----------------
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


# ---------------- keyboards ----------------
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

        # If driver has active trip, allow completion
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
    keyboard = [[KeyboardButton("📍 Share Location", request_location=True)]]
    return ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)


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
        # Shortcut when disabled
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


# ---------------- start & utility ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update.message.reply_text(
        f"Welcome to RideShare Bot, {user.first_name}! 🚗\n\n"
        "I help connect riders with drivers.\n"
        "Choose an option below:",
        reply_markup=get_main_menu_keyboard(user.id),
    )
    return MAIN_MENU


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Your Telegram User ID: `{user.id}`\n\n"
        "Add this ID into ADMIN_IDS in config.py to grant admin access.",
        parse_mode="Markdown",
    )


# ---------------- main menu handler ----------------
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
            "⏳ Your registration is pending approval.\n\n"
            "An admin will review your application soon.\n"
            "You'll be notified when approved.",
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

    if query.data == "complete_trip":
        trip = db.get_active_trip_for_driver(user.id)
        if not trip:
            await query.edit_message_text(
                "ℹ️ You don't have an active trip.",
                reply_markup=get_main_menu_keyboard(user.id),
            )
            return MAIN_MENU
        await query.edit_message_text(
            f"✅ *Complete Trip?*\n\nTrip #{trip['id']} at {trip['ride_time']}\n"
            "This will:\n"
            "• Mark the trip as completed\n"
            "• Ask riders & driver to leave ratings\n\nProceed?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Yes, complete", callback_data=f"complete_yes_{trip['id']}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="back_main")],
                ]
            ),
        )
        return COMPLETE_CONFIRM

    if query.data == "request_ride":
        if not db.is_user_approved(user.id):
            await query.edit_message_text(
                "❌ You need to be registered and approved to request rides.",
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


# ---------------- registration flow ----------------
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
        "Step 5/5: Choose *Priority* or Standard.\n\n"
        "Priority requests are matched first when multiple riders fit the same driver.",
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

    priority_fee_txt = ""
    if is_priority:
        priority_fee_txt = f"\n⚡ Priority fee: {matching.format_money(PRIORITY_FEE)}"

    surge_txt = ""
    if ENABLE_SURGE_PRICING and fare_info.get("surge_multiplier", 1.0) > 1.0:
        surge_txt = f"\n🔥 Surge: x{fare_info['surge_multiplier']}"

    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Request Ride", callback_data="confirm_ride")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]

    await query.edit_message_text(
        "💰 *Fare Estimate*\n\n"
        f"📏 Distance: {fare_info['distance_km']} km\n"
        f"🕐 Time: {context.user_data['ride_time']}\n"
        f"👥 Passengers: {context.user_data['passengers']}\n"
        f"{surge_txt}{priority_fee_txt}\n\n"
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

    await query.edit_message_text(
        "✅ *Ride Request Submitted!*\n\n"
        f"🕐 Time: {context.user_data['ride_time']}\n"
        f"👥 Passengers: {passengers}\n"
        f"💰 Estimated fare: {fare_info['formatted_per_passenger']} per person\n\n"
        "🔍 *Looking for a matching driver...*\n\n"
        "I'll notify you as soon as I find a match!",
        parse_mode="Markdown",
    )

    # Attempt immediate match
    rider = db.get_rider_by_user_id(user.id)
    if rider:
        match = matching.find_match_for_rider(rider)
        if match:
            match_details = matching.process_match(rider, match)
            await notify_match(context, match_details)

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

    await query.edit_message_text(
        "✅ *Ride Offer Submitted!*\n\n"
        f"🕐 Departure: {context.user_data['ride_time']}\n"
        f"💺 Seats available: {seats}\n\n"
        "🔍 *Looking for matching riders...*\n\n"
        "I'll notify you as soon as I find a match!",
        parse_mode="Markdown",
    )

    # Attempt immediate match
    driver = db.get_driver_by_user_id(user.id)
    if driver:
        match = matching.find_match_for_driver(driver)
        if match:
            match_details = matching.process_match(match, driver)
            await notify_match(context, match_details)

    await query.message.reply_text("What would you like to do next?", reply_markup=get_main_menu_keyboard(user.id))
    context.user_data.clear()
    return MAIN_MENU


# ---------------- matching notifications ----------------
async def notify_match(context: ContextTypes.DEFAULT_TYPE, match_details: Dict[str, Any]):
    """
    Notify rider & driver. Includes trip_id + price info.
    Also schedules departure reminders.
    """
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
    surge_mult = fare.get("surge_multiplier", 1.0)
    surge_txt = f"\n🔥 Surge: x{surge_mult}" if ENABLE_SURGE_PRICING and float(surge_mult) > 1.0 else ""

    trip_line = f"\n🧾 Trip ID: {trip_id}" if trip_id else ""

    rider_message = (
        "🎉 *Match Found!*\n"
        f"{trip_line}\n\n"
        "*Your Driver:*\n"
        f"👤 Name: {driver_name}\n"
        f"📱 Phone: {driver_phone}\n"
        f"💬 Telegram: {driver_contact}\n"
        f"🕐 Departure: {match_details['driver_time']}\n"
        f"💺 Seats left: {match_details.get('driver_seats_left', match_details['available_seats'])}\n"
    )
    if driver_info and driver_info.get("vehicle_type"):
        rider_message += f"🚗 Vehicle: {driver_info['vehicle_type']} ({driver_info.get('vehicle_year_model', 'N/A')})\n"

    rider_message += (
        "\n💰 *Fare Details:*\n"
        f"📏 Distance: {distance_km} km{surge_txt}\n"
        f"💵 Total: {fare_total}\n"
        f"👤 Your share: {fare_per_person}\n\n"
        "Please contact your driver to confirm pickup details."
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
        f"📏 Distance: {distance_km} km{surge_txt}\n"
        f"💵 Total to collect: {fare_total}\n\n"
        "Please contact your rider to confirm pickup details."
    )

    try:
        await context.bot.send_message(chat_id=rider_user_id, text=rider_message, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(rider_user_id))
    except Exception as e:
        logger.error("Failed to notify rider %s: %s", rider_user_id, e)

    try:
        await context.bot.send_message(chat_id=driver_user_id, text=driver_message, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(driver_user_id))
    except Exception as e:
        logger.error("Failed to notify driver %s: %s", driver_user_id, e)

    # Send locations to driver
    try:
        await context.bot.send_message(chat_id=driver_user_id, text="📍 *Rider's Pickup Location:*", parse_mode="Markdown")
        await context.bot.send_location(chat_id=driver_user_id, latitude=match_details["rider_pickup"][0], longitude=match_details["rider_pickup"][1])
        await context.bot.send_message(chat_id=driver_user_id, text="📍 *Rider's Drop-off Location:*", parse_mode="Markdown")
        await context.bot.send_location(chat_id=driver_user_id, latitude=match_details["rider_dropoff"][0], longitude=match_details["rider_dropoff"][1])
    except Exception as e:
        logger.error("Failed to send locations to driver: %s", e)

    # Schedule departure reminders (driver + rider)
    try:
        if REMIND_BEFORE_MINUTES > 0:
            depart_dt = matching.parse_time(match_details["driver_time"])
            remind_dt = depart_dt - timedelta(minutes=REMIND_BEFORE_MINUTES)
            now = datetime.now()
            if remind_dt > now:
                context.job_queue.run_once(
                    departure_reminder_job,
                    when=(remind_dt - now),
                    data={"user_ids": [rider_user_id, driver_user_id], "trip_id": trip_id, "time": match_details["driver_time"]},
                    name=f"depart_reminder_{trip_id}_{rider_user_id}_{driver_user_id}",
                )
    except Exception as e:
        logger.error("Failed to schedule reminders: %s", e)


# ---------------- history ----------------
async def history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    keyboard = [
        [InlineKeyboardButton("📜 Recent Trips", callback_data="history_trips")],
        [InlineKeyboardButton("⭐ My Ratings", callback_data="history_ratings")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
    ]
    await query.edit_message_text(
        "📜 *Ride History*\n\nChoose:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
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
            await query.edit_message_text(
                "📜 *Ride History*\n\nNo trips yet.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]),
            )
            return HISTORY_MENU

        lines = ["📜 *Recent Trips* (last 10)\n"]
        for t in items:
            status = t["status"]
            lines.append(
                f"• Trip #{t['id']} — {t['ride_time']} — {status} — seats {t['seats_filled']}/{t['total_seats']}"
            )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]),
        )
        return HISTORY_MENU

    if query.data == "history_ratings":
        summary = db.get_user_rating_summary(user.id)
        if not summary:
            await query.edit_message_text(
                "⭐ *My Ratings*\n\nNo ratings yet.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]),
            )
            return HISTORY_MENU

        await query.edit_message_text(
            "⭐ *My Ratings*\n\n"
            f"Average: {summary['avg_rating']} / 5\n"
            f"Total ratings: {summary['count']}\n",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="history")]]),
        )
        return HISTORY_MENU

    # Allow returning to history root
    if query.data == "history":
        return await history_menu(update, context)

    return HISTORY_MENU


# ---------------- trip completion + rating ----------------
async def complete_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "back_main":
        await query.edit_message_text("Choose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    if query.data.startswith("complete_yes_"):
        trip_id = int(query.data.replace("complete_yes_", ""))
        trip = db.get_trip_by_id(trip_id)
        if not trip or trip["driver_user_id"] != user.id:
            await query.edit_message_text("❌ Trip not found.", reply_markup=get_main_menu_keyboard(user.id))
            return MAIN_MENU

        db.complete_trip(trip_id)

        # Ask for ratings for each participant
        passenger_ids = [p["rider_user_id"] for p in db.get_trip_passengers(trip_id)]
        participants = list({user.id, *passenger_ids})

        for rater in participants:
            # each rater rates everyone else
            for ratee in participants:
                if rater == ratee:
                    continue
                if db.has_rating(trip_id, rater, ratee):
                    continue
                try:
                    await context.bot.send_message(
                        chat_id=rater,
                        text=f"⭐ Please rate your experience with user `{ratee}` for Trip #{trip_id}.",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Rate now", callback_data=f"rate_trip_{trip_id}_{ratee}")]]),
                    )
                except Exception as e:
                    logger.error("Failed to prompt rating: %s", e)

        await query.edit_message_text(
            f"✅ Trip #{trip_id} completed.\n\nRating prompts were sent to participants.",
            reply_markup=get_main_menu_keyboard(user.id),
        )
        return MAIN_MENU

    return COMPLETE_CONFIRM


async def rate_entry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point from inline 'Rate now' button."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not query.data.startswith("rate_trip_"):
        return MAIN_MENU

    _, _, trip_id_s, ratee_s = query.data.split("_", 3)
    trip_id = int(trip_id_s)
    ratee_user_id = int(ratee_s)

    context.user_data["rate_trip_id"] = trip_id
    context.user_data["rate_ratee_id"] = ratee_user_id

    await query.edit_message_text(
        f"⭐ Rate user `{ratee_user_id}` for Trip #{trip_id}\n\nChoose stars:",
        parse_mode="Markdown",
        reply_markup=rating_stars_keyboard(),
    )
    return RATE_STARS


async def rate_stars_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.", reply_markup=get_main_menu_keyboard(user.id))
        context.user_data.pop("rate_trip_id", None)
        context.user_data.pop("rate_ratee_id", None)
        return MAIN_MENU

    if not query.data.startswith("rate_"):
        return RATE_STARS

    stars = int(query.data.replace("rate_", ""))
    context.user_data["rate_stars"] = stars

    await query.edit_message_text(
        "📝 Optional: add a short comment (or send /skip to skip comment).",
        parse_mode="Markdown",
    )
    return RATE_COMMENT


async def rate_comment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    comment = (update.message.text or "").strip()

    trip_id = int(context.user_data.get("rate_trip_id", 0))
    ratee_id = int(context.user_data.get("rate_ratee_id", 0))
    stars = int(context.user_data.get("rate_stars", 0))

    if trip_id and ratee_id and 1 <= stars <= 5:
        db.add_rating(trip_id=trip_id, rater_user_id=user.id, ratee_user_id=ratee_id, rating=stars, comment=comment)
        await update.message.reply_text("✅ Thanks! Rating saved.", reply_markup=get_main_menu_keyboard(user.id))
    else:
        await update.message.reply_text("❌ Something went wrong. Try again.", reply_markup=get_main_menu_keyboard(user.id))

    context.user_data.pop("rate_trip_id", None)
    context.user_data.pop("rate_ratee_id", None)
    context.user_data.pop("rate_stars", None)
    return MAIN_MENU


async def rate_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip comment."""
    user = update.effective_user
    trip_id = int(context.user_data.get("rate_trip_id", 0))
    ratee_id = int(context.user_data.get("rate_ratee_id", 0))
    stars = int(context.user_data.get("rate_stars", 0))

    if trip_id and ratee_id and 1 <= stars <= 5:
        db.add_rating(trip_id=trip_id, rater_user_id=user.id, ratee_user_id=ratee_id, rating=stars, comment="")
        await update.message.reply_text("✅ Thanks! Rating saved.", reply_markup=get_main_menu_keyboard(user.id))
    else:
        await update.message.reply_text("❌ Something went wrong. Try again.", reply_markup=get_main_menu_keyboard(user.id))

    context.user_data.pop("rate_trip_id", None)
    context.user_data.pop("rate_ratee_id", None)
    context.user_data.pop("rate_stars", None)
    return MAIN_MENU


# ---------------- admin handlers ----------------
async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not is_admin(user.id):
        return MAIN_MENU

    if query.data == "admin_pending":
        pending = db.get_pending_registrations()
        if not pending:
            await query.edit_message_text("✅ No pending registrations.", reply_markup=get_admin_keyboard())
            return ADMIN_MENU
        context.user_data["pending_list"] = pending
        context.user_data["pending_index"] = 0
        return await show_pending_user(update, context)

    if query.data == "admin_report_reg":
        stats = db.get_registration_stats()
        report = (
            "📊 *Registration Report*\n\n"
            f"⏳ Pending: {stats['pending']}\n"
            f"✅ Approved: {stats['approved']}\n"
            f"❌ Rejected: {stats['rejected']}\n\n"
            f"🚙 Total Drivers: {stats['total_drivers']}\n"
            f"🚗 Total Passengers: {stats['total_passengers']}"
        )
        await query.edit_message_text(report, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "admin_report_trips":
        stats = db.get_trip_stats()
        report = (
            "🚗 *Trip Report*\n\n"
            f"Total Matches: {stats['total_matches']}\n"
            f"Total Trips: {stats['total_trips']}\n"
            f"Active Trips: {stats['active_trips']}\n"
            f"Completed Trips: {stats['completed_trips']}\n\n"
            "*Current Status:*\n"
            f"Riders Waiting: {stats['riders_waiting']}\n"
            f"Drivers Available: {stats['drivers_available']}"
        )
        await query.edit_message_text(report, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "admin_report_wait":
        stats = db.get_waiting_time_stats()
        report = (
            "⏱️ *Waiting Time Report*\n\n"
            f"Average Wait (matched): {stats['avg_wait_minutes']} min\n"
            f"Currently Waiting: {stats['currently_waiting']} riders\n"
            f"Current Avg Wait: {stats['current_avg_wait_minutes']} min"
        )
        await query.edit_message_text(report, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "admin_report_seats":
        stats = db.get_seat_utilization()
        report = (
            "💺 *Seat Utilization Report*\n\n"
            f"Total Seats Offered: {stats['total_seats_offered']}\n"
            f"Total Seats Filled: {stats['total_seats_filled']}\n"
            f"Total Seats Used (matches): {stats['total_seats_used']}\n"
            f"Utilization Rate: {stats['utilization_rate']}%"
        )
        await query.edit_message_text(report, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "admin_analytics":
        # Advanced analytics: peak times + trends
        peak = an.get_peak_times(limit=5)
        trends = an.get_daily_trends(days=7)
        surge = an.get_current_supply_demand()

        peak_lines = "\n".join([f"• {t['ride_time']}: {t['count']} requests/offers" for t in peak]) or "No data"
        trend_lines = "\n".join([f"• {d['date']}: {d['count']} trips created" for d in trends]) or "No data"

        report = (
            "📈 *Advanced Analytics*\n\n"
            "*Peak Times (top 5)*\n"
            f"{peak_lines}\n\n"
            "*Trips Created (last 7 days)*\n"
            f"{trend_lines}\n\n"
            "*Supply/Demand Now*\n"
            f"Riders searching: {surge['riders_waiting']}\n"
            f"Drivers available: {surge['drivers_available']}\n"
        )
        await query.edit_message_text(report, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "admin_users":
        users = db.get_all_users()
        if not users:
            await query.edit_message_text("No registered users.", reply_markup=get_admin_keyboard())
            return ADMIN_MENU

        user_list = "👥 *All Users*\n\n"
        for u in users[:25]:
            status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(u["status"], "❓")
            role_emoji = "🚙" if u["role"] == "driver" else "🚗"
            user_list += f"{status_emoji}{role_emoji} {u['name']} ({u['phone']})\n"
        if len(users) > 25:
            user_list += f"\n... and {len(users) - 25} more"

        await query.edit_message_text(user_list, parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    if query.data == "back_main":
        await query.edit_message_text("Choose an option:", reply_markup=get_main_menu_keyboard(user.id))
        return MAIN_MENU

    return ADMIN_MENU


async def show_pending_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pending = context.user_data.get("pending_list", [])
    index = context.user_data.get("pending_index", 0)

    if not pending or index >= len(pending):
        await query.edit_message_text("✅ No more pending registrations.", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    user_data = pending[index]
    context.user_data["reviewing_user_id"] = user_data["user_id"]

    info = (
        f"📋 *Registration #{index + 1}/{len(pending)}*\n\n"
        f"*Name:* {user_data['name']}\n"
        f"*Phone:* {user_data['phone']}\n"
        f"*Role:* {user_data['role'].title()}\n"
        f"*Document:* {user_data['document_type']}\n"
    )
    if user_data["role"] == "driver":
        info += (
            f"\n*Vehicle:* {user_data.get('vehicle_type')}\n"
            f"*Seats:* {user_data.get('vehicle_seats')}\n"
            f"*Year/Model:* {user_data.get('vehicle_year_model')}\n"
        )

    keyboard = [
        [InlineKeyboardButton("✅ Approve", callback_data="approve_user"), InlineKeyboardButton("❌ Reject", callback_data="reject_user")],
        [InlineKeyboardButton("📄 View Document", callback_data="view_document")],
        [InlineKeyboardButton("⏭️ Skip", callback_data="skip_user")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_pending_back")],
    ]
    await query.edit_message_text(info, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_VIEW_USER


async def admin_view_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    admin_id = update.effective_user.id

    if query.data == "approve_user":
        user_id = context.user_data.get("reviewing_user_id")
        if user_id and db.approve_user(user_id, admin_id):
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ *Registration Approved!*\n\nYou can now use the RideShare bot.\nType /start to begin.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("Failed to notify user %s: %s", user_id, e)
            await query.edit_message_text("✅ User approved!")
        else:
            await query.edit_message_text("❌ Failed to approve user.")

        context.user_data["pending_index"] = context.user_data.get("pending_index", 0) + 1
        context.user_data["pending_list"] = db.get_pending_registrations()
        return await show_pending_user(update, context)

    if query.data == "reject_user":
        user_id = context.user_data.get("reviewing_user_id")
        if user_id and db.reject_user(user_id, admin_id):
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ *Registration Rejected*\n\nPlease contact support or try registering again.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("Failed to notify user %s: %s", user_id, e)
            await query.edit_message_text("❌ User rejected.")
        else:
            await query.edit_message_text("❌ Failed to reject user.")

        context.user_data["pending_index"] = context.user_data.get("pending_index", 0) + 1
        context.user_data["pending_list"] = db.get_pending_registrations()
        return await show_pending_user(update, context)

    if query.data == "view_document":
        pending = context.user_data.get("pending_list", [])
        index = context.user_data.get("pending_index", 0)
        if pending and index < len(pending):
            doc_path = pending[index].get("document_path")
            if doc_path and os.path.exists(doc_path):
                await context.bot.send_photo(chat_id=admin_id, photo=open(doc_path, "rb"), caption="📄 User's document")
            else:
                await query.message.reply_text("❌ Document not found.")
        return ADMIN_VIEW_USER

    if query.data == "skip_user":
        context.user_data["pending_index"] = context.user_data.get("pending_index", 0) + 1
        return await show_pending_user(update, context)

    if query.data == "admin_pending_back":
        await query.edit_message_text("🔧 *Admin Panel*\n\nSelect an option:", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        return ADMIN_MENU

    return ADMIN_VIEW_USER


# ---------------- background jobs (reminders + auto-match) ----------------
async def periodic_jobs(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs:
    - auto-match loop (tries to match any waiting riders/drivers)
    - reminders for riders close to timeout
    """
    try:
        # Auto-match: priority riders first
        riders = db.get_searching_riders_sorted()
        drivers = db.get_available_drivers()
        for rider in riders:
            m = matching.find_match_for_rider(rider, drivers_cache=drivers)
            if m:
                details = matching.process_match(rider, m)
                await notify_match(context, details)
                # refresh driver cache because seats can change
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


# ---------------- generic cancel/fallback ----------------
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


# ---------------- app bootstrap ----------------
def build_application() -> Application:
    db.init_db()  # includes migrations

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                main_menu_handler,
                pattern=r"^(register|status_pending|my_profile|history|complete_trip|request_ride|offer_ride|cancel_active|admin_panel|back_main)$"
            ),
        ],

        states={
            MAIN_MENU: [
                CallbackQueryHandler(
                    main_menu_handler,
                    pattern=r"^(register|status_pending|my_profile|history|complete_trip|request_ride|offer_ride|cancel_active|admin_panel|back_main)$"
                )
            ],

            # Registration
            REG_ROLE: [
                CallbackQueryHandler(reg_role_handler, pattern=r"^(reg_passenger|reg_driver|cancel)$")
            ],

            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name_handler)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone_handler)],
            REG_DOCUMENT_TYPE: [CallbackQueryHandler(reg_document_type_handler)],
            REG_DOCUMENT: [MessageHandler(filters.PHOTO, reg_document_handler)],
            REG_VEHICLE_TYPE: [CallbackQueryHandler(reg_vehicle_type_handler)],
            REG_VEHICLE_SEATS: [CallbackQueryHandler(reg_vehicle_seats_handler)],
            REG_VEHICLE_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_vehicle_year_handler)],
            # Rider
            RIDER_PICKUP: [MessageHandler(filters.LOCATION, rider_pickup_location), CallbackQueryHandler(cancel_handler, pattern="^cancel$")],
            RIDER_DROPOFF: [MessageHandler(filters.LOCATION, rider_dropoff_location), CallbackQueryHandler(cancel_handler, pattern="^cancel$")],
            RIDER_TIME: [CallbackQueryHandler(rider_time_selection)],
            RIDER_PASSENGERS: [CallbackQueryHandler(rider_passengers_selection)],
            RIDER_PRIORITY: [CallbackQueryHandler(rider_priority_selection)],
            RIDER_CONFIRM_FARE: [CallbackQueryHandler(rider_confirm_fare)],
            # Driver
            DRIVER_START: [MessageHandler(filters.LOCATION, driver_start_location), CallbackQueryHandler(cancel_handler, pattern="^cancel$")],
            DRIVER_END: [MessageHandler(filters.LOCATION, driver_end_location), CallbackQueryHandler(cancel_handler, pattern="^cancel$")],
            DRIVER_TIME: [CallbackQueryHandler(driver_time_selection)],
            DRIVER_SEATS: [CallbackQueryHandler(driver_seats_selection)],
            # History
            HISTORY_MENU: [CallbackQueryHandler(history_handler)],
            # Completion
            COMPLETE_CONFIRM: [CallbackQueryHandler(complete_confirm_handler)],
            # Rating
            RATE_STARS: [CallbackQueryHandler(rate_stars_handler)],
            RATE_COMMENT: [
                CommandHandler("skip", rate_skip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, rate_comment_handler),
            ],
            # Admin
            ADMIN_MENU: [CallbackQueryHandler(admin_menu_handler)],
            ADMIN_VIEW_USER: [CallbackQueryHandler(admin_view_user_handler)],
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
    # Rating entry point can happen anytime
    application.add_handler(CallbackQueryHandler(rate_entry_handler, pattern=r"^rate_trip_"))

    # Background repeating job
    application.job_queue.run_repeating(periodic_jobs, interval=REMINDER_CHECK_INTERVAL_SECONDS, first=10)

    return application


def main():
    app = build_application()
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
